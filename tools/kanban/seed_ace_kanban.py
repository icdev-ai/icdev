# CUI // SP-CTI
"""Seed Kanban tasks for the ANVIL Co-Worker Engine (ACE) initiative.

Project: ace  (task_prefix 'ace-')
Epics: infra, foundation, runtime, chat, qa, canvas, register
Plan: C:/Users/schuo/.claude/plans/please-think-about-and-tender-kettle.md

ACE introduces dynamic agentic co-worker teams assembled from declarative YAML
role templates, communicating via delegation/creator-verifier/negotiation/broadcast,
built entirely on existing ICDEV infrastructure (A2A, TeamOrchestrator, RICOAS,
HITL, CodeLens, Genesis, mailbox, LLMRouter).

42 atomic Kanban tasks seeded across 7 epics with dependency chain.
depends_on_task_id is single-parent (engine limitation).
Secondary prerequisites are described in the task description text.

Run:
    python tools/kanban/seed_ace_kanban.py
    python tools/kanban/seed_ace_kanban.py --dry-run
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

PROJECT_ID = "ace"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC infra -- Project Infrastructure
    # =========================================================================
    {
        "id": "ace-infra-01",
        "title": "Register ACE project in args/projects.yaml with 7 epics",
        "description": (
            "Add the ACE project block to args/projects.yaml: "
            "key=ace, task_prefix='ace-', name='ANVIL Co-Worker Engine (ACE)', "
            "description (dynamic agentic co-worker teams via YAML role templates + "
            "ICDEV infrastructure), default_open=true. Epics: infra, foundation, "
            "runtime, chat, qa, canvas, register -- each with title and priority. "
            "Verify: python -c \"import yaml; d=yaml.safe_load(open('args/projects.yaml')); "
            "p=[x for x in d['projects'] if x['key']=='ace'][0]; print(len(p['epics']))\" "
            "Expected: 7. This is the root dependency for all ACE tasks."
        ),
        "task_type": "chore",
        "priority": "critical",
        "depends_on_task_id": None,
    },
    {
        "id": "ace-infra-02",
        "title": "Create args/ace/ directory structure with ace_config.yaml",
        "description": (
            "Create the FORGE args layer for ACE: "
            "args/ace/ace_config.yaml -- global config with: max_team_size=8, "
            "max_negotiation_rounds=3, hitl_threshold=0.6, trust_tier_default=yellow, "
            "stale_instance_hours=4, hot_reload_roles=true. "
            "Create dirs: args/ace/roles/, args/ace/prompt_chains/, "
            "args/ace/hitl_templates/. Add .gitkeep to each empty dir. "
            "Verify: python -c \"import yaml; c=yaml.safe_load(open('args/ace/ace_config.yaml')); "
            "print(c['max_team_size'])\" Expected: 8."
        ),
        "task_type": "chore",
        "priority": "critical",
        "depends_on_task_id": "ace-infra-01",
    },
    {
        "id": "ace-infra-03",
        "title": "Create tools/manifest/ace-coworker-engine.md manifest shard",
        "description": (
            "Create tools/manifest/ace-coworker-engine.md documenting all ACE "
            "modules: ACEController (controller.py), ProblemClassifierLens "
            "(problem_classifier.py), TeamAssembler (team_assembler.py), "
            "RoleLoader (role_loader.py), CoWorkerThread (coworker_thread.py), "
            "StepExecutor (step_executor.py), MessageBus (message_bus.py), "
            "ace_team_monitor reflex (genesis_reflex.py). "
            "Follow existing shard format (tool path, description, key functions, "
            "args config key, DB tables). "
            "Add reference link in tools/manifest.md index under a new "
            "'ANVIL Co-Worker Engine (ACE)' entry."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": "ace-infra-01",
    },
    {
        "id": "ace-infra-04",
        "title": "Add ACE security gate to args/security_gates.yaml",
        "description": (
            "Add a gate entry to args/security_gates.yaml: "
            "name=ace_stale_hitl, level=warning, "
            "condition='any ace_instances row with state=hitl_pending "
            "and started_at < NOW() - INTERVAL 24 hours', "
            "action='block deploy', "
            "description='ACE team awaiting HITL approval for > 24h -- resolve before deploy'. "
            "Follow the existing gate entry format."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ace-infra-01",
    },
    {
        "id": "ace-infra-05",
        "title": "Create goals/ace_coworker.md goal workflow document",
        "description": (
            "Create goals/ace_coworker.md defining the ACE goal workflow: "
            "trigger (chat/kanban/api), problem classification (Oracle lens), "
            "team assembly (role YAMLs), co-worker execution (step loop), "
            "communication primitives (delegate/verify/negotiate/broadcast), "
            "HITL gates, result surfacing (chat + /coworker/ canvas). "
            "Add entry to goals/manifest.md: "
            "| ACE Co-Worker | goals/ace_coworker.md | Dynamic agentic co-worker "
            "teams: delegation, creator-verifier, negotiation, broadcast |"
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ace-infra-01",
    },

    # =========================================================================
    # EPIC foundation -- Phase 1: Role system + classifier + assembler + DB
    # =========================================================================
    {
        "id": "ace-foundation-01",
        "title": "Create icdev/tools/ace/__init__.py + constants.py",
        "description": (
            "Create the icdev.tools.ace namespace. "
            "__init__.py: empty package init. "
            "constants.py: "
            "ACE_MESSAGE_TYPES = ('cw_broadcast','cw_delegate','cw_verify_request',"
            "'cw_verify_response','cw_negotiate_propose','cw_negotiate_counter',"
            "'cw_negotiate_accept','cw_negotiate_reject'); "
            "INSTANCE_STATES = ['assembling','running','hitl_pending','completed',"
            "'failed','aborted']; "
            "COWORKER_STATES = ['idle','working','waiting','verifying',"
            "'negotiating','done','failed']; "
            "APPEND_ONLY_TABLES = ('ace_audit_log',); "
            "ACE_CANVAS_DB_ENV = 'ICDEV_ACE_DB_URL'; "
            "MAX_TEAM_SIZE = 8; MAX_NEGOTIATION_ROUNDS = 3. "
            "SQL CHECK constraints in init_db.py must derive from these constants."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-infra-02",
    },
    {
        "id": "ace-foundation-02",
        "title": "Create icdev/tools/ace/db/__init__.py + init_db.py -- 5 canvas tables",
        "description": (
            "Create icdev/tools/ace/db/__init__.py (empty) and init_db.py. "
            "init_db.py must use get_canvas_connection('ICDEV_ACE_DB_URL') -- "
            "NOT get_connection() (canvas tables have no classification/tenant_id). "
            "Create 5 tables: ace_instances (state CHECK from INSTANCE_STATES), "
            "ace_coworkers (state CHECK from COWORKER_STATES), "
            "ace_messages (message_type TEXT), "
            "ace_artifacts (artifact_type, classification), "
            "ace_audit_log (append-only, never UPDATE/DELETE). "
            "All INDEXes on instance_id FKs. "
            "Gracefully handle 'table already exists'. "
            "Verify: python -m icdev.tools.ace.db.init_db && "
            "python -c \"from icdev.tools.ace.db.init_db import init; init()\" -- no errors."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-01",
    },
    {
        "id": "ace-foundation-03",
        "title": "Create args/ace/roles/ai_developer.yaml -- AI Developer role template",
        "description": (
            "Create the reference AI Developer role template at "
            "args/ace/roles/ai_developer.yaml. "
            "Fields: role_id=ai_developer, display_name, description, version=1.0, "
            "trust_tier=yellow, default_count=1, max_instances=3. "
            "Steps: intake (process_message_for_intake), analysis "
            "(PromptChainExecutor ace_developer_analysis chain), hitl_review "
            "(WorkflowEngine.create_instance with template_id=ace_developer_review, "
            "required=true), build (A2AAgentClient.send_task to builder-agent, "
            "depends_on=hitl_review), verify_request (MessageBus.send cw_verify_request "
            "to qa_manager). "
            "communication: can_delegate=true, can_verify=true, can_negotiate=true, "
            "broadcast_on_completion=true. llm_function=code_generation. "
            "tool_permissions: requirement_intake_hook, workflow_hitl.engine, "
            "a2a.agent_client, ace.message_bus."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-infra-02",
    },
    {
        "id": "ace-foundation-04",
        "title": "Create args/ace/roles/qa_manager.yaml -- QA Manager role template",
        "description": (
            "Create the reference QA Manager role template at "
            "args/ace/roles/qa_manager.yaml. "
            "Fields: role_id=qa_manager, trust_tier=yellow, default_count=1, max_instances=1. "
            "Steps: codelens_scan (CodeLens.analyze_file on build_result.primary_file), "
            "coherence_check (CoherenceChecker.run_all gate=true), "
            "e2e_run (e2e_runner.run_all), "
            "autofix (MessageBus.negotiate cw_negotiate_propose to ai_developer, "
            "condition=e2e_result.failed_count > 0, max_rounds=3), "
            "verify_response (MessageBus.send cw_verify_response to ai_developer "
            "with passed=e2e_result.failed_count==0). "
            "communication: can_delegate=false, can_verify=true, can_negotiate=true. "
            "llm_function=code_review. "
            "tool_permissions: analysis.code_lens, workflow.coherence_checker, "
            "testing.e2e_runner, ace.message_bus."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-03",
    },
    {
        "id": "ace-foundation-05",
        "title": "Create icdev/tools/ace/role_loader.py -- YAML role loader with hot-reload",
        "description": (
            "Create icdev/tools/ace/role_loader.py. "
            "RoleLoader class: loads all *.yaml from args/ace/roles/, "
            "validates required fields (role_id, steps, trust_tier, tool_permissions), "
            "caches in-memory with 60s TTL hot-reload in dev mode. "
            "Public API: get_role(role_id: str) -> RoleTemplate, "
            "list_roles() -> list[RoleTemplate], reload() -> int (count loaded). "
            "RoleTemplate dataclass: role_id, display_name, description, version, "
            "trust_tier, default_count, max_instances, steps, communication, "
            "llm_function, tool_permissions, genesis_reflex. "
            "Raises RoleNotFoundError if role_id unknown. "
            "Verify: python -c \"from icdev.tools.ace.role_loader import RoleLoader; "
            "r=RoleLoader(); print(r.list_roles())\" -- shows ai_developer, qa_manager."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-01",
    },
    {
        "id": "ace-foundation-06",
        "title": "Create icdev/tools/ace/problem_classifier.py -- ProblemClassifierLens",
        "description": (
            "Create icdev/tools/ace/problem_classifier.py. "
            "ProblemClassifierLens(BaseLens) from tools/oracle/base_lens.py. "
            "analyze(problem_text) -> dict with RICOAS signals (from "
            "tools/chat/requirement_intake_hook.py patterns), keyword scores, "
            "role catalog (from RoleLoader.list_roles()). "
            "score(analysis) -> list[OraclePrediction] ranked by confidence. "
            "propose(predictions) -> TeamManifest dataclass with "
            "list[RoleSlot(role_id, count, priority)]. "
            "Fallback: if max confidence < 0.5, return [ai_developer(1), qa_manager(1)]. "
            "TeamManifest and RoleSlot are dataclasses in this file. "
            "Uses LLMRouter.invoke('task_decomposition') for LLM-assisted role suggestion "
            "when pattern confidence is insufficient."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-05",
    },
    {
        "id": "ace-foundation-07",
        "title": "Create icdev/tools/ace/team_assembler.py -- TeamManifest to CoWorkerSpec list",
        "description": (
            "Create icdev/tools/ace/team_assembler.py. "
            "TeamAssembler.assemble(manifest: TeamManifest, instance_id: str, "
            "context: dict) -> TeamInstance. "
            "CoWorkerSpec dataclass: coworker_id, role_id, role_slot, mailbox_id, "
            "llm_function, tool_permissions, trust_tier. "
            "TeamInstance dataclass: instance_id, specs: list[CoWorkerSpec], "
            "workflow_id (from agent_workflows insert). "
            "Creates ace_instances row (state=assembling) and ace_coworkers rows "
            "(state=idle) via get_canvas_connection(). "
            "Respects MAX_TEAM_SIZE from constants. "
            "Registers session via tools/coordination/session_registry.py "
            "(session_type='ace', intent=problem_text[:100])."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-06",
    },
    {
        "id": "ace-foundation-08",
        "title": "Write tests/test_ace_foundation.py -- role loading, classifier, DB",
        "description": (
            "Write tests/test_ace_foundation.py covering: "
            "test_role_loader_loads_yaml() -- RoleLoader loads ai_developer + qa_manager; "
            "test_role_loader_missing_raises() -- RoleNotFoundError on unknown role_id; "
            "test_problem_classifier_fallback() -- short input returns default team; "
            "test_problem_classifier_build_request() -- 'build a REST API' scores "
            "ai_developer high; "
            "test_team_assembler_creates_db_rows() -- TeamAssembler.assemble() "
            "inserts ace_instances + ace_coworkers rows; "
            "test_db_tables_exist() -- init_db creates all 5 tables. "
            "Use conftest.py MINIMAL_ICDEV_SCHEMA pattern (in-memory SQLite). "
            "Run: pytest tests/test_ace_foundation.py -v"
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-07",
    },

    # =========================================================================
    # EPIC runtime -- Phase 2: Message Bus + Co-Worker Runtime + Controller
    # =========================================================================
    {
        "id": "ace-runtime-01",
        "title": "Create icdev/tools/ace/message_bus.py -- ACE message routing over mailbox",
        "description": (
            "Create icdev/tools/ace/message_bus.py. "
            "MessageBus wraps tools/agent/mailbox.py WITHOUT modifying its DB CHECK. "
            "Transport: message_type='notification', subject='ACE:{cw_type}' "
            "(e.g. 'ACE:cw_verify_request'). ACE semantic type stored in ace_messages. "
            "MessageBus(instance_id: str): "
            "send(from_coworker_id, to_role, message_type, payload) -> str; "
            "broadcast(from_coworker_id, message_type, payload) -> list[str]; "
            "negotiate(from_coworker_id, to_role, payload, max_rounds=3) -> dict; "
            "poll_inbox(coworker_id, timeout_s=5) -> list[dict]. "
            "negotiate() raises NegotiationFailedError after max_rounds. "
            "_resolve_role(to_role) -> mailbox_id looks up ace_coworkers table. "
            "All send operations insert into ace_messages table."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-07",
    },
    {
        "id": "ace-runtime-02",
        "title": "Create icdev/tools/ace/step_executor.py -- dynamic tool invocation",
        "description": (
            "Create icdev/tools/ace/step_executor.py. "
            "StepExecutor.run(step: dict, context: dict, spec: CoWorkerSpec, "
            "trust_kernel: TrustKernelBase) -> any. "
            "1. Resolve dotted path in step['tool'] via importlib (must be in spec.tool_permissions). "
            "2. Substitute $variable references in step['args'] from context dict. "
            "3. Call trust_kernel.can_execute(spec.trust_tier, step['id']) -- raise "
            "TrustKernelDeniedError if denied. "
            "4. Execute tool function with resolved args. "
            "5. Handle step['condition'] -- skip step if condition evaluates False. "
            "6. Store result under step['output_var'] in context. "
            "7. Emit ace_audit_log row after each step. "
            "Raises ToolPermissionDeniedError for tools not in spec.tool_permissions."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-runtime-01",
    },
    {
        "id": "ace-runtime-03",
        "title": "Create icdev/tools/ace/coworker_thread.py -- CoWorkerThread execution unit",
        "description": (
            "Create icdev/tools/ace/coworker_thread.py. "
            "CoWorkerThread(threading.Thread): "
            "Constructor: spec, instance_id, message_bus, trust_kernel. "
            "run() loop: "
            "1. Load role via RoleLoader.get_role(spec.role_id). "
            "2. Update ace_coworkers.state = 'working'. "
            "3. For each step: call StepExecutor.run(step, context, spec, trust_kernel). "
            "4. Between steps: poll_inbox() for incoming messages -- handle "
            "cw_verify_request (suspend, run verify steps, resume) and "
            "cw_negotiate_propose (enter negotiation handler). "
            "5. On step.required=true failure: create HITL instance, set state=hitl_pending. "
            "6. On completion: send cw_broadcast 'done', update state='done'. "
            "7. On exception: set state='failed', emit error to ace_audit_log. "
            "HITL wait uses HITLGate.get_pending() poll loop."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-runtime-02",
    },
    {
        "id": "ace-runtime-04",
        "title": "Create icdev/tools/ace/controller.py -- ACEController singleton",
        "description": (
            "Create icdev/tools/ace/controller.py. "
            "ACEController singleton (initialized at app startup): "
            "launch(problem_text, trigger_source, trigger_ref, user_id='system', "
            "project_id='') -> str (instance_id). "
            "Internal: ProblemClassifier.run(problem_text) -> TeamManifest, "
            "TeamAssembler.assemble() -> TeamInstance, "
            "ThreadPoolExecutor submit each CoWorkerThread, "
            "Register in session_registry, emit SSE progress event. "
            "Returns instance_id immediately (non-blocking). "
            "CLI entry point: python -m icdev.tools.ace.controller --launch TEXT [--json] "
            "--status INSTANCE_ID [--json] "
            "--abort INSTANCE_ID "
            "--list-roles. "
            "Verify: python -m icdev.tools.ace.controller --list-roles -- prints ai_developer, qa_manager."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-runtime-03",
    },
    {
        "id": "ace-runtime-05",
        "title": "Write tests/test_ace_runtime.py -- message bus, thread, controller",
        "description": (
            "Write tests/test_ace_runtime.py covering: "
            "test_message_bus_send_inserts_ace_messages() -- send() creates ace_messages row; "
            "test_message_bus_broadcast_fans_out() -- 2 co-workers both receive; "
            "test_message_bus_negotiate_accept() -- propose then accept completes; "
            "test_message_bus_negotiate_max_rounds() -- NegotiationFailedError after 3; "
            "test_coworker_thread_step_sequence() -- mock StepExecutor, verify step order; "
            "test_coworker_thread_trust_denied() -- TrustKernelDeniedError halts thread; "
            "test_controller_launch_returns_instance_id() -- launch() non-blocking, "
            "ace_instances row created with state=assembling. "
            "Run: pytest tests/test_ace_runtime.py -v"
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "ace-runtime-04",
    },

    # =========================================================================
    # EPIC chat -- Phase 3: Chat Integration
    # =========================================================================
    {
        "id": "ace-chat-01",
        "title": "Create tools/extensions/builtins/020_coworker_trigger.py -- chat hook",
        "description": (
            "Create tools/extensions/builtins/020_coworker_trigger.py. "
            "Hook type: chat_message_after, PRIORITY=20 (fires after 010_ai_governance). "
            "NAME = 'coworker_trigger'. "
            "Trigger conditions (OR): "
            "1. Explicit: message contains '@team', '@coworkers', '/ace', or phrases "
            "('assemble a team', 'bring in co-workers', 'spin up co-workers'). "
            "2. Implicit: RICOAS score >= 4 requirements found AND len(content) > 200 "
            "AND context key 'coworker_opted_out' is not True. "
            "On trigger: ACEController.launch(problem_text, 'chat', context_id), "
            "inject system message via chat_manager.send_message() role='assistant' "
            "with content announcing team assembly + instance_id link to /coworker/. "
            "Set context['coworker_instance_id'] = instance_id."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-runtime-04",
    },
    {
        "id": "ace-chat-02",
        "title": "Extend tools/dashboard/chat_manager.py -- coworker_instance_id linking",
        "description": (
            "Minor additive change to tools/dashboard/chat_manager.py. "
            "After _fire_intake_hook() call in send_message(), add "
            "_check_coworker_trigger(context_id, content, context) call "
            "that reads context.get('coworker_instance_id') set by the extension hook "
            "and stores the link. "
            "No existing behavior changed -- purely additive. "
            "The chat_context_metadata column (or equivalent JSON field) stores the link "
            "so the chat UI can show a 'View Co-Worker Team' button. "
            "Verify: existing chat tests still pass with no modification."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "ace-chat-01",
    },
    {
        "id": "ace-chat-03",
        "title": "Add get_coworker_instances() to tools/chat/cli_bridge.py",
        "description": (
            "Add get_coworker_instances(context_id: str) -> list[dict] to "
            "tools/chat/cli_bridge.py. "
            "Looks up ace_instances by trigger_ref=context_id. "
            "Returns list of {instance_id, state, created_at, team_manifest}. "
            "Used by CLI/headless sessions to poll ACE status for a chat context. "
            "Also add dashboard_url_coworker(instance_id: str) -> str helper "
            "returning 'http://localhost:5050/coworker/{instance_id}'."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "ace-chat-02",
    },
    {
        "id": "ace-chat-04",
        "title": "Write tests/test_ace_chat_integration.py -- chat trigger E2E",
        "description": (
            "Write tests/test_ace_chat_integration.py covering: "
            "test_explicit_trigger_fires_ace() -- '@team build REST API' creates ace_instances; "
            "test_implicit_trigger_long_requirements() -- 200+ char requirements message "
            "with 4+ RICOAS signals creates instance; "
            "test_no_trigger_short_message() -- short casual message does NOT create instance; "
            "test_chat_manager_stores_coworker_link() -- context stores coworker_instance_id; "
            "test_cli_bridge_get_coworker_instances() -- returns instance for context_id. "
            "Use mock for ACEController.launch() to avoid actual thread spawning. "
            "Run: pytest tests/test_ace_chat_integration.py -v"
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "ace-chat-03",
    },

    # =========================================================================
    # EPIC qa -- Phase 4: QA Co-Worker Reference Implementation
    # =========================================================================
    {
        "id": "ace-qa-01",
        "title": "Create args/ace/prompt_chains/ace_developer_analysis.yaml -- 4-step chain",
        "description": (
            "Create args/ace/prompt_chains/ace_developer_analysis.yaml. "
            "4-step prompt chain for AI Developer analysis step "
            "(used by PromptChainExecutor from tools/agent/prompt_chain_executor.py): "
            "Step 1 (model=architect): Enumerate 2-4 interpretations of the requirement. "
            "Step 2 (model=security): Flag security concerns for each interpretation. "
            "Step 3 (model=architect): Recommend implementation approach with justification. "
            "Step 4 (model=compliance): Verify approach against NIST 800-53 AC controls. "
            "Variable: $INPUT = requirements text from RICOAS intake. "
            "Output: structured JSON with interpretations, recommended_approach, "
            "security_flags, compliance_gaps."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-runtime-04",
    },
    {
        "id": "ace-qa-02",
        "title": "Create args/ace/hitl_templates/ace_developer_review.yaml -- HITL template",
        "description": (
            "Create args/ace/hitl_templates/ace_developer_review.yaml. "
            "HITL policy template for the AI Developer review gate "
            "(registered with WorkflowEngine.create_instance). "
            "Stages: build (automated) -> review (manual, role=developer) -> "
            "approve (manual, role=tenant_admin). "
            "approval_policy=any_one, kickback_limit=3. "
            "required_docs for review stage: RICOAS analysis checklist. "
            "Register with workflow_hitl/template_manager.py: "
            "call TemplateManager.register_template(yaml_path) on app startup "
            "from blueprint.py before_app_first_request or init_db. "
            "Verify: python -c \"from tools.workflow_hitl.template_manager import "
            "TemplateManager; t=TemplateManager(); print(t.get('ace_developer_review'))\" "
            "-- not None."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-qa-01",
    },
    {
        "id": "ace-qa-03",
        "title": "Finalize ai_developer.yaml -- complete analysis + build + verify steps",
        "description": (
            "Update args/ace/roles/ai_developer.yaml to include all 5 steps: "
            "intake (process_message_for_intake), "
            "analysis (PromptChainExecutor.run_chain, chain=ace_developer_analysis), "
            "hitl_review (WorkflowEngine.create_instance, template=ace_developer_review, "
            "required=true), "
            "build (A2AAgentClient.send_task to builder-agent, depends_on=[hitl_review]), "
            "verify_request (MessageBus.send cw_verify_request to qa_manager). "
            "Ensure $variable substitutions are consistent: "
            "$instance_id, $problem_text, $intake_result, $analysis_result, "
            "$builder_agent_url (read from args/agent_config.yaml or registry), "
            "$build_result. "
            "Verify: RoleLoader.get_role('ai_developer').steps has 5 entries."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-qa-02",
    },
    {
        "id": "ace-qa-04",
        "title": "Finalize qa_manager.yaml -- CodeLens + Coherence + E2E + negotiate + verify",
        "description": (
            "Update args/ace/roles/qa_manager.yaml to include all 5 steps: "
            "codelens_scan (CodeLens.analyze_file, path=$build_result.primary_file), "
            "coherence_check (CoherenceChecker.run_all, gate=true), "
            "e2e_run (e2e_runner.run_all), "
            "autofix (MessageBus.negotiate, message_type=cw_negotiate_propose, "
            "to_role=ai_developer, payload=$e2e_result.failures, max_rounds=3, "
            "condition=$e2e_result.failed_count > 0), "
            "verify_response (MessageBus.send cw_verify_response to ai_developer, "
            "payload={passed: $e2e_result.failed_count == 0, details: $e2e_result}). "
            "Verify: RoleLoader.get_role('qa_manager').steps has 5 entries."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-qa-03",
    },
    {
        "id": "ace-qa-05",
        "title": "Create icdev/tools/ace/genesis_reflex.py -- ace_team_monitor reflex",
        "description": (
            "Create icdev/tools/ace/genesis_reflex.py. "
            "Function run(config: dict, db_conn) -- SUPPORT tier reflex. "
            "Queries ace_instances WHERE state IN ('running','hitl_pending') "
            "AND started_at < NOW() - INTERVAL '4 hours'. "
            "For each stale instance: "
            "- If state=running: set state=failed, emit ace_audit_log event. "
            "- If state=hitl_pending: escalate via WorkflowEngine.escalate(). "
            "Logs total stale count to genesis_audit table. "
            "Returns {'stale_running': int, 'stale_hitl': int}. "
            "Verify: python -c \"from icdev.tools.ace.genesis_reflex import run; "
            "print(run.__doc__)\" -- no ImportError."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "ace-runtime-04",
    },
    {
        "id": "ace-qa-06",
        "title": "Register ace_team_monitor in reflex_registry.py + daemon.py",
        "description": (
            "1. tools/genesis/reflex_registry.py: add ReflexEntry("
            "'ace_team_monitor', SUPPORT, 6.0, "
            "'ACE: detect and escalate stale co-worker instances'). "
            "SUPPORT tier import: from icdev.tools.ace.genesis_reflex import run as ace_team_monitor_run. "
            "2. tools/genesis/daemon.py: add 'ace_team_monitor' to REFLEX_NAMES list "
            "(alphabetically or at end of SUPPORT section). "
            "Verify: python -c \"from tools.genesis.reflex_registry import list_reflexes; "
            "print([r.name for r in list_reflexes() if 'ace' in r.name])\" "
            "Expected: ['ace_team_monitor']."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "ace-qa-05",
    },
    {
        "id": "ace-qa-07",
        "title": "Write tests/test_ace_qa_workflow.py -- negotiation scenario test",
        "description": (
            "Write tests/test_ace_qa_workflow.py covering the full AI Developer + "
            "QA Manager interaction: "
            "test_qa_manager_sends_verify_response_on_pass() -- e2e_result.failed_count=0 "
            "causes cw_verify_response with passed=True; "
            "test_qa_manager_negotiates_on_failure() -- failed_count>0 triggers "
            "cw_negotiate_propose to ai_developer; "
            "test_negotiation_accept_resolves() -- ai_developer sends cw_negotiate_accept, "
            "negotiate() returns; "
            "test_negotiation_max_rounds_hitl() -- after 3 rounds no accept, "
            "NegotiationFailedError creates HITL instance; "
            "test_genesis_reflex_escalates_stale() -- reflex with stale instance "
            "calls WorkflowEngine.escalate(). "
            "Run: pytest tests/test_ace_qa_workflow.py -v"
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "ace-qa-04",
    },

    # =========================================================================
    # EPIC canvas -- Phase 5: Dashboard Canvas (all 8 required components)
    # =========================================================================
    {
        "id": "ace-canvas-01",
        "title": "Create tools/dashboard/templates/coworker/index.html -- active teams grid",
        "description": (
            "Create tools/dashboard/templates/coworker/index.html. "
            "Extends base.html. Shows: "
            "1. Active teams grid -- cards per ace_instances row (instance_id, state badge, "
            "problem_text preview, team_manifest role chips, started_at, duration). "
            "2. SSE live updates via EventSource('/api/ace/events') updating state badges. "
            "3. 'Launch Team' button opening a modal with problem_text textarea + submit. "
            "4. IQE widget: {% include 'includes/iqe_query_widget.html' %} with "
            "iqe_api_route='/api/ace/iqe-query'. "
            "5. Link to /coworker/roles role catalog. "
            "CUI banner at top. Use existing Tailwind/Bootstrap classes from other canvas templates."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-runtime-04",
    },
    {
        "id": "ace-canvas-02",
        "title": "Create tools/dashboard/templates/coworker/instance.html -- instance detail",
        "description": (
            "Create tools/dashboard/templates/coworker/instance.html. "
            "Shows for a single ACE instance: "
            "1. Instance header: state badge, trigger source, problem_text, started_at. "
            "2. Co-worker status row: each co-worker as a card with role_name, state, "
            "current_step, steps completed progress bar. "
            "3. Message thread timeline: ace_messages in chronological order with "
            "message_type badge (color-coded), from/to co-workers, body preview. "
            "4. Artifacts panel: list of ace_artifacts (type, title, content_inline preview). "
            "5. Abort button (POST /api/ace/{instance_id}/abort, confirm modal). "
            "SSE for live co-worker state updates."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-canvas-01",
    },
    {
        "id": "ace-canvas-03",
        "title": "Create tools/dashboard/templates/coworker/roles.html -- role catalog",
        "description": (
            "Create tools/dashboard/templates/coworker/roles.html. "
            "Displays all loaded role templates from RoleLoader.list_roles(). "
            "For each role: display_name, description, trust_tier badge, "
            "step count, tool_permissions list, llm_function. "
            "Shows how to add a new role (YAML file path + minimal schema). "
            "Breadcrumb: Co-Worker Teams / Roles. "
            "Static data rendered at request time (hot-reloads every 60s in dev)."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "ace-canvas-01",
    },
    {
        "id": "ace-canvas-04",
        "title": "Create icdev/tools/ace/blueprint.py -- Flask Blueprint with 9 routes",
        "description": (
            "Create icdev/tools/ace/blueprint.py. "
            "ace_bp = Blueprint('ace', __name__, url_prefix='/coworker'). "
            "Routes: "
            "GET  /             -> render index.html (active teams) "
            "GET  /<instance_id>-> render instance.html "
            "GET  /roles        -> render roles.html "
            "POST /api/ace/launch -> ACEController.launch(), return {instance_id} "
            "GET  /api/ace/instances -> list with pagination "
            "GET  /api/ace/<id>/status -> state + coworker states "
            "GET  /api/ace/<id>/messages -> message thread "
            "GET  /api/ace/<id>/artifacts -> artifacts list "
            "POST /api/ace/<id>/abort -> set state=aborted "
            "POST /api/ace/iqe-query -> IQE natural-language query. "
            "All data routes use get_canvas_connection(). "
            "before_request: call init_db() once. "
            "Import ACEController at module level -- no ImportError at startup."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-canvas-01",
    },
    {
        "id": "ace-canvas-05",
        "title": "Register ace canvas in tools/dashboard/app.py (_CANVAS_DEFS + iqe_dispatch)",
        "description": (
            "Two changes to tools/dashboard/app.py: "
            "1. Add to _CANVAS_DEFS list: "
            "(\"ace\", \"ICDEV_ACE_ENABLED\", \"icdev.tools.ace.blueprint\", \"ace_bp\"). "
            "Default off (not in _CANVAS_DEFAULTS_TRUE). "
            "2. Add 'ace' entry to iqe_dispatch() _CANVAS_MAP: "
            "{'ace': 'icdev.tools.iqe.adapters.ace'}. "
            "Verify: ICDEV_ACE_ENABLED=true flask run -- /coworker/ returns 200."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ace-canvas-04",
    },
    {
        "id": "ace-canvas-06",
        "title": "Add /coworker/ to base.html nav Ops dropdown + PATH_CANVAS array",
        "description": (
            "Two changes to tools/dashboard/templates/base.html: "
            "1. In Ops dropdown nav section add: "
            "<a href='/coworker/'>Co-Worker Teams</a> "
            "(after 'Agents' link, before end of Ops section). "
            "2. In PATH_CANVAS JavaScript array add: "
            "[/^\\/coworker/, 'ace']. "
            "This enables IQE mini-bar context switching when on /coworker/* routes. "
            "Verify: /coworker/ page has breadcrumb and IQE mini-bar shows 'ace' canvas."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-canvas-05",
    },
    {
        "id": "ace-canvas-07",
        "title": "Create tools/iqe/adapters/ace.py -- 3 IQE collections",
        "description": (
            "Create tools/iqe/adapters/ace.py. "
            "Register 3 collections via register_collection() at module import: "
            "'ace.instances' -> instances_adapter(conn) -- queries ace_instances, "
            "returns list[dict] with instance_id, state, trigger_source, problem_text[:80], "
            "created_at. "
            "'ace.coworkers' -> coworkers_adapter(conn) -- queries ace_coworkers JOIN "
            "ace_instances, returns role_id, state, current_step, instance_id. "
            "'ace.messages' -> messages_adapter(conn) -- queries ace_messages, "
            "returns from_coworker, to_coworker, message_type, created_at, body_json[:80]. "
            "All adapters use get_canvas_connection('ICDEV_ACE_DB_URL'). "
            "Verify: python -c \"import tools.iqe.adapters.ace\" -- no error."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ace-canvas-04",
    },
    {
        "id": "ace-canvas-08",
        "title": "Create context/iqe/queries/ace/ with 3 seed queries",
        "description": (
            "Create 3 seed IQE query files: "
            "context/iqe/queries/ace/01_active_teams.md -- "
            "'foreach i in ace.instances where i.state == \"running\" select *' "
            "context/iqe/queries/ace/02_failed_instances.md -- "
            "'foreach i in ace.instances where i.state == \"failed\" select *' "
            "context/iqe/queries/ace/03_coworker_states.md -- "
            "'foreach c in ace.coworkers where c.state != \"done\" select *'. "
            "Each file follows the existing IQE seed format: "
            "# Title, ## Query, IQE block, ## Description explaining what it shows."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ace-canvas-07",
    },
    {
        "id": "ace-canvas-09",
        "title": "Mirror coworker templates to icdev/tools/dashboard/templates/coworker/",
        "description": (
            "Create icdev/tools/dashboard/templates/coworker/ directory and copy "
            "(or symlink) the 3 coworker templates: index.html, instance.html, roles.html. "
            "This is the icdev/ package mirror required by the 8-component canvas gate. "
            "Ideally run: python tools/dx/companion.py --sync --write --json "
            "to let companion sync handle it automatically. "
            "If companion.py is not yet aware of the ace canvas, manually copy files. "
            "Verify: both paths exist and are identical: "
            "tools/dashboard/templates/coworker/*.html and "
            "icdev/tools/dashboard/templates/coworker/*.html"
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ace-canvas-03",
    },

    # =========================================================================
    # EPIC register -- Phase 5b: 8-point registration checklist
    # =========================================================================
    {
        "id": "ace-register-01",
        "title": "Add ace_audit_log to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py",
        "description": (
            "Add 'ace_audit_log' to the APPEND_ONLY_TABLES tuple in "
            ".claude/hooks/pre_tool_use.py. "
            "This prevents any UPDATE or DELETE on the ace_audit_log table "
            "(NIST AU compliance, same pattern as wf_feedback, genesis_audit, etc.). "
            "Verify: python .claude/hooks/pre_tool_use.py --check-tables -- "
            "ace_audit_log appears in protected list. "
            "Also verify constants.py APPEND_ONLY_TABLES matches."
        ),
        "task_type": "chore",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-02",
    },
    {
        "id": "ace-register-02",
        "title": "Add ACE table schemas to tests/conftest.py MINIMAL_ICDEV_SCHEMA",
        "description": (
            "Add all 5 ACE table CREATE TABLE statements to the MINIMAL_ICDEV_SCHEMA "
            "dict in tests/conftest.py. "
            "This ensures unit tests that create in-memory SQLite DBs include ACE tables. "
            "Follow the existing pattern: schema string keyed by table name. "
            "Include: ace_instances, ace_coworkers, ace_messages, ace_artifacts, "
            "ace_audit_log. "
            "Verify: pytest tests/test_ace_foundation.py::test_db_tables_exist -v -- PASS."
        ),
        "task_type": "chore",
        "priority": "critical",
        "depends_on_task_id": "ace-foundation-02",
    },
    {
        "id": "ace-register-03",
        "title": "Register ace_launch + ace_status in MCP tool_registry.py + gap_handlers.py",
        "description": (
            "Register two MCP tools in tools/mcp/tool_registry.py: "
            "1. ace_launch(problem_text: str, trigger_source: str = 'api') -> dict "
            "-- calls ACEController.launch(), returns {instance_id, state}. "
            "2. ace_status(instance_id: str) -> dict "
            "-- returns full instance status with co-worker states. "
            "Add corresponding gap_handler entries in tools/mcp/gap_handlers.py "
            "following existing pattern. "
            "Verify: python -c \"from tools.mcp.tool_registry import list_tools; "
            "print([t for t in list_tools() if 'ace' in t])\" "
            "Expected: ['ace_launch', 'ace_status']."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ace-runtime-04",
    },
    {
        "id": "ace-register-04",
        "title": "Update docs/reference/commands.md with ACE CLI commands",
        "description": (
            "Add ACE CLI commands section to docs/reference/commands.md: "
            "python -m icdev.tools.ace.controller --launch 'problem text' [--json] "
            "python -m icdev.tools.ace.controller --status <instance_id> [--json] "
            "python -m icdev.tools.ace.controller --abort <instance_id> "
            "python -m icdev.tools.ace.controller --list-roles "
            "python tools/kanban/seed_ace_kanban.py [--dry-run] "
            "ICDEV_ACE_ENABLED=true  # env var to enable /coworker/ canvas. "
            "Place in the 'ANVIL Co-Worker Engine' subsection under the Tools section."
        ),
        "task_type": "chore",
        "priority": "low",
        "depends_on_task_id": "ace-runtime-04",
    },
]


def seed(dry_run: bool = False) -> None:
    conn = get_connection()
    cur = conn.cursor()
    ts = _now()

    inserted = 0
    skipped = 0

    for task in TASKS:
        cur.execute("SELECT id FROM kanban_tasks WHERE id = %s", (task["id"],))
        if cur.fetchone():
            skipped += 1
            continue

        if not dry_run:
            cur.execute(
                """
                INSERT INTO kanban_tasks
                    (id, title, description, task_type, priority, status,
                     depends_on_task_id, project_id, created_at, updated_at,
                     classification)
                VALUES
                    (%s, %s, %s, %s, %s, 'backlog',
                     %s, %s, %s, %s, 'CUI')
                """,
                (
                    task["id"],
                    task["title"],
                    task.get("description", ""),
                    task.get("task_type", "feature"),
                    task.get("priority", "medium"),
                    task.get("depends_on_task_id"),
                    PROJECT_ID,
                    ts,
                    ts,
                ),
            )
        print(f"{'[DRY]' if dry_run else '[OK]':6s} {task['id']:35s} {task['title'][:55]}")
        inserted += 1

    if not dry_run:
        conn.commit()

    print(
        f"\n{'Dry-run' if dry_run else 'Seeded'}: {inserted} new tasks, "
        f"{skipped} already existed. Project: {PROJECT_ID}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed ACE Kanban tasks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)
