# CUI // SP-CTI
"""Seed Kanban tasks for two implementation epics:
  Epic 1 (ai-*): AADC↔AIMC Deep Integration (9 tasks)
  Epic 2 (am-*): Application Migration Module (13 tasks)

Run:
    python tools/kanban/seed_aadc_aimc_appmigration.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC 1: AADC ↔ AIMC DEEP INTEGRATION
    # =========================================================================
    # Priority values: 'critical' | 'high' | 'medium' | 'low'
    {
        "id": "ai-01",
        "title": "DB migration — aimc_models table (move FOUNDATION_MODELS from constants to DB)",
        "description": (
            "Create a new DB migration under tools/db/migrations/ that adds the `aimc_models` table "
            "with columns: id TEXT PK, name, provider, family, type, context_window INT, vram_gb INT, "
            "cost_per_1k_tokens REAL, il_suitability TEXT (JSON array), air_gap_ready INT, "
            "quantization_options TEXT (JSON), notes TEXT, is_deprecated INT DEFAULT 0, "
            "deprecation_date TEXT, created_at, updated_at. "
            "Seed all 40 FOUNDATION_MODELS entries from tools/aiml_canvas/constants.py into this table. "
            "Update tools/aiml_canvas/constants.py so FOUNDATION_MODELS reads from DB (with fallback to "
            "hardcoded list if DB unavailable). Update canvas_bridge.py get_aimc_catalog() to query "
            "aimc_models table instead of FOUNDATION_MODELS constant."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": None,
    },
    {
        "id": "ai-02",
        "title": "AIMC model CRUD API — POST/PUT/DELETE /api/models routes",
        "description": (
            "Add three new routes to tools/aiml_canvas/blueprint.py: "
            "POST /api/models (create model from JSON body, writes to aimc_models table), "
            "PUT /api/models/<model_id> (update fields including il_suitability, air_gap_ready, notes), "
            "DELETE /api/models/<model_id> (soft-delete: set is_deprecated=1, deprecation_date=now). "
            "Validate that il_suitability is a list of ints in [2,4,5,6]. "
            "Use tools/aiml_canvas/db/init_db.py get_connection(). "
            "Return the updated model dict on success. "
            "Requires ai-01 (aimc_models table must exist)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ai-01",
    },
    {
        "id": "ai-03",
        "title": "AIMC bus_subscriber.py — publish model.added / model.updated / model.deprecated events",
        "description": (
            "Create tools/aiml_canvas/bus_subscriber.py following the AADC bus_subscriber pattern. "
            "Add register() function (call from create_aiml_blueprint() factory). "
            "Add three publish helpers: publish_model_added(model_id, name, il_suitability), "
            "publish_model_updated(model_id, changed_fields), "
            "publish_model_deprecated(model_id, deprecation_date). "
            "Call these helpers from the POST/PUT/DELETE /api/models routes added in ai-02. "
            "All events target_canvas='aadc'. "
            "Use tools/canvas/event_bus.py publish(). "
            "Requires ai-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ai-02",
    },
    {
        "id": "ai-04",
        "title": "AADC bus_subscriber — handle aimc.model.deprecated, flag orphaned model refs",
        "description": (
            "Add a new handler _on_aimc_model_deprecated() to "
            "tools/agentic_ai_canvas/bus_subscriber.py. "
            "Handler signature: (event_id, canvas_id, event_type, payload). "
            "Subscribe in register(): subscribe('aadc', 'aimc.model.deprecated', _on_aimc_model_deprecated). "
            "Handler logic: query aadc_aimc_model_refs WHERE aimc_model_id = payload['model_id']; "
            "for each ref, write an aadc_audit row with action='AIMC_MODEL_DEPRECATED' and "
            "detail including the ref_id, aadc_design_id, and deprecation_date; "
            "also write an aadc_artifacts row with artifact_type='aimc_deprecated_model_ref' "
            "so the AADC UI can surface it as a warning. "
            "Requires ai-03."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "ai-03",
    },
    {
        "id": "ai-05",
        "title": "Genesis reflex aimc_orphan_refs.py — scan stale aadc_aimc_model_refs (4h cadence)",
        "description": (
            "Create tools/genesis/reflexes/aimc_orphan_refs.py following the bdc_isa_expiry.py pattern. "
            "CADENCE_HOURS = 4. "
            "run(ctx, conn) logic: "
            "(1) Load all active model IDs from aimc_models table (is_deprecated=0); "
            "(2) Query aadc_aimc_model_refs for refs where aimc_model_id NOT IN that set; "
            "(3) For each orphan ref, publish canvas event 'aadc.model_ref.orphaned' with "
            "{ref_id, aadc_design_id, aimc_model_id}; "
            "(4) Return {refs_checked, orphans_found, events_published, errors, status}. "
            "Also handle deprecated models: query WHERE aimc_model_id IN (SELECT id FROM aimc_models "
            "WHERE is_deprecated=1) and emit 'aadc.model_ref.deprecated'. "
            "Requires ai-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ai-01",
    },
    {
        "id": "ai-06",
        "title": "AIMC blueprint — /api/models/<id>/impact route (AADC design impact analysis)",
        "description": (
            "Add GET /api/models/<model_id>/impact to tools/aiml_canvas/blueprint.py. "
            "Response: { "
            "  model_id, model_name, "
            "  aadc_refs_count: int,  (total AADC designs using this model) "
            "  il_violations: [ {aadc_design_id, design_name, il_level, violations: [...]} ], "
            "  deprecated: bool, "
            "  impact_severity: 'none'|'low'|'medium'|'high'  (high if any CAT1 violations) "
            "}. "
            "Call canvas_bridge.get_aadc_refs_for_model(model_id) then "
            "canvas_bridge.check_il_compatibility(design_id) for each ref. "
            "This gives AIMC operators a pre-deprecation impact assessment before removing a model. "
            "Requires ai-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "ai-01",
    },
    {
        "id": "ai-07",
        "title": "AIMC blueprint — model deprecation UI badge + deprecation_date display",
        "description": (
            "Update tools/aiml_canvas/blueprint.py GET /api/models route to include "
            "is_deprecated and deprecation_date fields from aimc_models table in response. "
            "Update tools/dashboard/templates/aiml_canvas/ model catalog UI template: "
            "add a red 'DEPRECATED' badge on model cards where is_deprecated=1; "
            "show deprecation_date tooltip; "
            "show impact count badge (from /api/models/<id>/impact aadc_refs_count) for all models. "
            "Requires ai-06 (impact route needed for badge data)."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "ai-06",
    },
    {
        "id": "ai-08",
        "title": "AADC canvas JS — verify and fix model linker panel (AIMC catalog dropdown in node editor)",
        "description": (
            "Investigate tools/dashboard/templates/agentic_ai_canvas/ JS for the canvas node editor. "
            "When a user selects a node in the AADC canvas and opens node properties: "
            "(1) If the node type is an AI agent/LLM type, a 'Link AIMC Model' dropdown should appear "
            "fetching from GET /agentic-ai/api/aimc-catalog; "
            "(2) On selection, POST to /agentic-ai/api/designs/<id>/link-model; "
            "(3) Linked model name and IL status should appear in the node panel. "
            "If JS logic is missing or broken, implement it. "
            "Verify IL badge (green=compatible, red=CAT1 violation) renders correctly. "
            "No backend changes — frontend only. "
            "Independent of other ai-* tasks."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": None,
    },
    {
        "id": "ai-09",
        "title": "V&V — AADC-AIMC integration coherence + E2E + companion sync",
        "description": (
            "Validation gate for the full AADC-AIMC epic: "
            "(1) python tools/workflow/coherence_checker.py --all --gate — must pass; "
            "(2) python -m ruff check tools/aiml_canvas/ tools/agentic_ai_canvas/ — zero errors; "
            "(3) Verify aimc_models table seeded: python -c "
            "\"from tools.aiml_canvas.constants import FOUNDATION_MODELS; assert len(FOUNDATION_MODELS)>=40\"; "
            "(4) Dry-run aimc_orphan_refs reflex: "
            "python -c \"from tools.genesis.reflexes.aimc_orphan_refs import run; r=run({'dry_run':True}); "
            "assert r['status']=='ok'\"; "
            "(5) python tools/dx/companion.py --sync --write --json. "
            "Depends on ai-07 (last non-V&V task in chain)."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ai-07",
    },

    # =========================================================================
    # EPIC 2: APPLICATION MIGRATION MODULE
    # =========================================================================
    {
        "id": "am-01",
        "title": "DB migration — mc_app_inventory table (application CMDB)",
        "description": (
            "Create a new migration under tools/db/migrations/ that adds mc_app_inventory to "
            "migration_canvas.db. Schema: "
            "id TEXT PK, session_id TEXT (FK → mc_srv_sessions.id nullable), "
            "name TEXT NOT NULL, version TEXT, language TEXT, framework TEXT, "
            "app_type TEXT (web, api, batch, database, middleware, desktop, mobile, iot, saas), "
            "owner TEXT, team TEXT, criticality TEXT (mission_critical|high|medium|low), "
            "environment TEXT (production|staging|development), "
            "stig_category TEXT (cat1|cat2|cat3|na), "
            "license_type TEXT, license_expiry TEXT, "
            "source_repo TEXT, artifact_url TEXT, "
            "dependencies_json TEXT DEFAULT '[]', "
            "migration_strategy TEXT (rehost|replatform|refactor|rearchitect|repurchase|retire|retain), "
            "migration_status TEXT DEFAULT 'pending', "
            "notes TEXT, classification TEXT DEFAULT 'CUI', "
            "created_at TEXT, updated_at TEXT. "
            "Also add index on session_id and criticality. "
            "Run migration against both SQLite and PostgreSQL backends."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": None,
    },
    {
        "id": "am-02",
        "title": "App inventory CRUD API — routes in tools/migration_canvas/blueprint.py",
        "description": (
            "Add application inventory CRUD routes to tools/migration_canvas/blueprint.py: "
            "GET /api/app-inventory — list all app records (optional filter: session_id, criticality, environment); "
            "POST /api/app-inventory — create app record from JSON body; "
            "GET /api/app-inventory/<app_id> — get single app; "
            "PUT /api/app-inventory/<app_id> — update fields; "
            "DELETE /api/app-inventory/<app_id> — delete. "
            "POST /api/app-inventory/import — bulk import from CSV (columns: name, version, language, "
            "framework, app_type, owner, criticality, environment, stig_category, license_type). "
            "All routes protected by @mdc_login_required. "
            "Uses tools/migration_canvas/db/init_db.py get_connection(). "
            "Requires am-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "am-01",
    },
    {
        "id": "am-03",
        "title": "DB migration — mc_app_server_bindings table (app↔server many-to-many)",
        "description": (
            "Create migration adding mc_app_server_bindings table: "
            "id TEXT PK, app_id TEXT NOT NULL REFERENCES mc_app_inventory(id) ON DELETE CASCADE, "
            "server_id TEXT NOT NULL REFERENCES mc_srv_inventory(id) ON DELETE CASCADE, "
            "role TEXT (primary|replica|standby|worker|scheduler), "
            "port INT, protocol TEXT, "
            "notes TEXT, created_at TEXT. "
            "UNIQUE(app_id, server_id, role). "
            "Also add mc_app_data_sources table: "
            "id TEXT PK, app_id TEXT FK → mc_app_inventory, "
            "source_type TEXT (postgresql|mysql|oracle|mssql|mongodb|redis|elasticsearch|s3|sftp|api), "
            "host TEXT, port INT, database_name TEXT, schema_version TEXT, "
            "estimated_size_gb REAL, replication_lag_seconds INT, "
            "migration_method TEXT (dump_restore|cdc|replication|manual), "
            "migration_status TEXT DEFAULT 'pending', notes TEXT, created_at TEXT. "
            "Requires am-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "am-01",
    },
    {
        "id": "am-04",
        "title": "App-to-server binding API + wave_planner.py app-awareness",
        "description": (
            "Add binding routes to blueprint.py: "
            "POST /api/app-inventory/<app_id>/servers — bind app to server (creates mc_app_server_bindings row); "
            "GET /api/app-inventory/<app_id>/servers — list servers for app; "
            "DELETE /api/app-inventory/<app_id>/servers/<server_id> — unbind; "
            "GET /api/server-migration/inventory/<server_id>/apps — list apps on a server. "
            "Update tools/migration_canvas/wave_planner.py: "
            "In the wave generation logic, when grouping servers into waves, also group all apps "
            "bound to those servers into the same wave. "
            "Add field app_ids_json TEXT to mc_migration_waves table via ALTER TABLE. "
            "The wave summary response should include app_count and app_names. "
            "Requires am-03."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "am-03",
    },
    {
        "id": "am-05",
        "title": "DB migration — mc_app_dependencies table (transitive app dependency graph)",
        "description": (
            "Create migration adding mc_app_dependencies table: "
            "id TEXT PK, source_app_id TEXT REFERENCES mc_app_inventory(id), "
            "target_app_id TEXT REFERENCES mc_app_inventory(id) NULLABLE, "
            "target_server_id TEXT REFERENCES mc_srv_inventory(id) NULLABLE, "
            "target_service TEXT (database|cache|message_queue|api|auth|storage|cdn|monitor), "
            "dep_type TEXT (hard|soft|optional), "
            "protocol TEXT, port INT, "
            "latency_sla_ms INT, "
            "notes TEXT, created_at TEXT. "
            "CHECK: at least one of (target_app_id, target_server_id) IS NOT NULL. "
            "Index on source_app_id and target_app_id. "
            "Also add mc_app_migration_steps table: "
            "id TEXT PK, app_id TEXT FK, step_order INT, phase TEXT (pre|cutover|post|rollback), "
            "action TEXT, command TEXT, expected_output TEXT, timeout_seconds INT, "
            "status TEXT DEFAULT 'pending', executed_at TEXT, result TEXT, created_at TEXT. "
            "Requires am-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "am-01",
    },
    {
        "id": "am-06",
        "title": "App dependency graph API — routes + transitive closure endpoint",
        "description": (
            "Add dependency graph routes to blueprint.py: "
            "POST /api/app-inventory/<app_id>/dependencies — add dependency edge; "
            "GET /api/app-inventory/<app_id>/dependencies — list direct deps; "
            "DELETE /api/app-inventory/<app_id>/dependencies/<dep_id> — remove edge; "
            "GET /api/app-inventory/<app_id>/dependency-graph — transitive closure as "
            "{nodes: [{id, name, type}], edges: [{source, target, dep_type, service}]}. "
            "Transitive closure algorithm: BFS from app_id following source_app_id→target_app_id "
            "edges (max depth 10 to prevent cycles). "
            "GET /api/app-inventory/migration-order — topological sort of all apps by dependencies "
            "(apps with no deps migrate first). Returns ordered list with wave assignment suggestion. "
            "Requires am-05."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "am-05",
    },
    {
        "id": "am-07",
        "title": "Seed app-level SOP templates — Java EE, .NET, Python, Oracle DB, PostgreSQL",
        "description": (
            "Add 5 application migration SOP templates to mc_sops via a seeder in "
            "tools/migration_canvas/sops.py or a new seed function. "
            "Each template should have: sop_type='application', approval_status='draft', "
            "nist_controls JSON mapping to AU-2, CM-3, CM-5, SA-22. "
            "Templates: "
            "(1) Java EE → Containerized (phases: WAR export, Dockerfile, K8s deploy, smoke test, rollback); "
            "(2) .NET Framework → .NET Core (phases: compatibility scan, code migration, test, deploy); "
            "(3) Python App Replatform (phases: requirements freeze, container build, env config, test); "
            "(4) Oracle DB → PostgreSQL (phases: schema export, pgloader, data validation, cutover); "
            "(5) PostgreSQL Major Version Upgrade (phases: pg_upgrade dry-run, backup, upgrade, validate). "
            "Each SOP should have 6-10 steps_json with action, description, rollback_action. "
            "Independent — no prerequisite tasks. Can run in parallel with other am-* tasks."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "am-08",
        "title": "Extend post_migration_validator.py — HTTP smoke test + API health check + DB connectivity",
        "description": (
            "Update tools/migration_canvas/post_migration_validator.py to add 3 new validation checks "
            "for application-level post-migration validation: "
            "(1) http_smoke_test(url, expected_status=200, timeout=10): GET the app URL, "
            "check status code, response time < 5s; "
            "(2) api_health_check(base_url, health_path='/health', expected_keys=['status']): "
            "GET /health endpoint, check JSON response contains expected keys and status=='ok'; "
            "(3) db_connectivity_check(host, port, db_type, database, timeout=5): "
            "TCP connect to DB port, confirm accept; for PostgreSQL optionally run SELECT 1. "
            "All checks use stdlib (urllib, socket) — no new deps. "
            "Add run_app_validation(app_id, session_id) public function that runs all 3 checks "
            "for an app and writes results to mc_srv_post_migration_tests table "
            "(or a new mc_app_post_migration_tests table if schema doesn't fit). "
            "Independent — no prerequisite tasks."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "am-09",
        "title": "DB migration — mc_data_migration table + basic data migration planner API",
        "description": (
            "Create migration adding mc_data_migration table: "
            "id TEXT PK, session_id TEXT, app_id TEXT REFERENCES mc_app_inventory(id), "
            "source_type TEXT (postgresql|mysql|oracle|mssql|mongodb|redis|files|s3), "
            "source_host TEXT, source_db TEXT, source_schema TEXT, "
            "target_type TEXT, target_host TEXT, target_db TEXT, target_schema TEXT, "
            "migration_method TEXT (dump_restore|cdc|pgloader|mysqldump|mongodump|rsync|aws_dms), "
            "estimated_size_gb REAL, estimated_duration_minutes INT, "
            "validation_query TEXT (SQL to verify row counts match), "
            "validation_status TEXT DEFAULT 'pending', "
            "cutover_type TEXT (offline|online_with_cdc|snapshot), "
            "rollback_procedure TEXT, "
            "status TEXT DEFAULT 'planned', started_at TEXT, completed_at TEXT, "
            "notes TEXT, classification TEXT DEFAULT 'CUI', created_at TEXT. "
            "Add API routes: POST /api/data-migration, GET /api/data-migration/<id>, "
            "PUT /api/data-migration/<id>/status. "
            "Independent — no prerequisite tasks."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": None,
    },
    {
        "id": "am-10",
        "title": "DataBridge ServiceNow CMDB connector — pull app inventory automatically",
        "description": (
            "Create tools/databridge/connectors/servicenow_connector.py following the "
            "SaaSBaseConnector pattern (tools/databridge/connectors/saas_base.py). "
            "_connector_name = 'servicenow_cmdb'. "
            "_default_base_url = 'https://{instance}.service-now.com'. "
            "_endpoints: { "
            "  'applications': '/api/now/table/cmdb_ci_appl', "
            "  'servers': '/api/now/table/cmdb_ci_server', "
            "  'relationships': '/api/now/table/cmdb_rel_ci', "
            "  'databases': '/api/now/table/cmdb_ci_database' }. "
            "_build_auth_headers: Basic auth (username:password) or Bearer token. "
            "_extract_rows: unwrap ServiceNow envelope {'result': [...]}. "
            "health_check: GET /api/now/table/sys_properties?sysparm_limit=1. "
            "Add GET /api/app-inventory/import/servicenow route to migration_canvas blueprint: "
            "instantiates ServiceNowConnector, pulls cmdb_ci_appl, maps fields to mc_app_inventory schema, "
            "bulk-inserts. Independent task."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": None,
    },
    {
        "id": "am-11",
        "title": "App migration compliance gate — STIG category + license validation checks",
        "description": (
            "Extend tools/migration_canvas/compliance_gate.py with application-specific checks: "
            "(1) check_app_stig(session_id): for each mc_app_inventory row with stig_category='cat1', "
            "verify migration_strategy is NOT 'rehost' (CAT1 STIG apps must be refactored or replaced); "
            "emit CAT1 finding if violated. "
            "(2) check_license_expiry(session_id): for apps with license_expiry set, warn if expiry "
            "is within 90 days of estimated cutover date; CAT2 finding. "
            "(3) check_app_dependency_order(session_id): verify that all hard dependencies between apps "
            "are in wave order (dependency target must migrate before or same wave as source); "
            "CAT2 finding for out-of-order waves. "
            "Integrate these checks into the existing run_compliance_gate(session_id) function. "
            "Requires am-04 (wave order), am-06 (dependency graph), am-01 (app inventory). "
            "Use depends_on_task_id for am-06 as the last prerequisite."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "am-06",
    },
    {
        "id": "am-12",
        "title": "E2E tests — app inventory CRUD, binding, dependency graph, data migration planner",
        "description": (
            "Add test file tests/e2e_selenium/test_app_migration_module.py covering: "
            "(1) App inventory: POST (create), GET (list + filter by criticality), PUT (update strategy), DELETE; "
            "(2) CSV bulk import: POST /api/app-inventory/import with sample CSV, verify row count; "
            "(3) App-to-server binding: bind app to server, list servers for app, verify wave includes app; "
            "(4) Dependency graph: create A→B dep, GET /api/app-inventory/A/dependency-graph, "
            "verify B appears as node; "
            "(5) Migration order: GET /api/app-inventory/migration-order, verify B before A (B has no deps); "
            "(6) Data migration: POST /api/data-migration, GET by id, PUT status='in_progress'; "
            "(7) Post-validation: verify http_smoke_test and db_connectivity_check return structured dict. "
            "Follow existing test patterns from test_server_migration_canvas.py. "
            "Requires am-06, am-09, am-08."
        ),
        "task_type": "test",
        "priority": "medium",
        "depends_on_task_id": "am-06",
    },
    {
        "id": "am-13",
        "title": "V&V — Application Migration Module coherence + companion sync",
        "description": (
            "Validation gate for the full Application Migration epic: "
            "(1) python tools/workflow/coherence_checker.py --all --gate — must pass 0 failures; "
            "(2) python -m ruff check tools/migration_canvas/ tools/databridge/connectors/servicenow_connector.py; "
            "(3) Verify tables exist: python -c \"from tools.migration_canvas.db.init_db import get_connection; "
            "conn=get_connection(); tables=[r[0] for r in conn.execute(\\\"SELECT name FROM sqlite_master "
            "WHERE type='table'\\\").fetchall()]; "
            "assert 'mc_app_inventory' in tables; assert 'mc_app_server_bindings' in tables; "
            "assert 'mc_app_dependencies' in tables; print('Tables OK')\"; "
            "(4) python -m pytest tests/e2e_selenium/test_app_migration_module.py -x -q; "
            "(5) python tools/dx/companion.py --sync --write --json. "
            "Requires am-12."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "am-12",
    },
]


def seed():
    # Two epics with distinct prefixes (ai-* / am-*) share this batch; tag each
    # task with its own project_id so the project cards split correctly.
    tasks = []
    for t in TASKS:
        t = dict(t)
        t["project_id"] = "am" if t["id"].startswith("am-") else "ai"
        tasks.append(t)

    report = create_tasks("ai", tasks, strict=False, register_project=False)
    print(report.summary())
    return len(report.seeded)


if __name__ == "__main__":
    n = seed()
    print(f"\nTotal tasks queued: {n}")
    print("\nEpic 1 — AADC↔AIMC Integration (ai-01 → ai-09)")
    print("Epic 2 — Application Migration Module (am-01 → am-13)")
