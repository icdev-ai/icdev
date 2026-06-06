#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed Kanban tasks for Migration Canvas Enhancements (mce-*).

6 epics, 32 tasks total. Phase gates: CodeLens + Coherence + E2E Playwright
after each epic (fnd, cg, inv, wp, dg, inn).

Run: python tools/kanban/seed_mce_kanban.py
"""


import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402


_NOW = datetime.now(timezone.utc).isoformat()


TASKS = [
    # ═══════════════════════════════════════════════════════════════════════
    # EP1: FOUNDATION — Creative domain config + Innovation maintenance
    # Entry point — starts immediately, no deps
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "mce-fnd-01",
        "title": "MCE: Create context/creative/domains/migration.json",
        "description": (
            "Create new file: context/creative/domains/migration.json\n\n"
            "Must include:\n"
            "  domain: migration\n"
            "  display_name: Cloud & Server Migration\n"
            "  g2_category_url: https://www.g2.com/categories/cloud-migration-software\n"
            "  capterra_category_url: https://www.capterra.com/cloud-migration-software/\n"
            "  trustradius_category_url: https://www.trustradius.com/cloud-migration\n"
            "  additional_g2_urls: [data-migration-software, application-migration-tools, application-modernization-tools]\n"
            "  keywords: [cloud migration, server migration, lift and shift, rehost, P2V, P2C, V2C,\n"
            "    cutover, wave planning, workload migration, GovCloud migration, FedRAMP migration]\n"
            "  competitor_seeds: [Carbonite Migrate, Zerto, Veeam, AWS Migration Hub, Azure Migrate,\n"
            "    CloudEndure, RiverMeadow, PlateSpin Migrate, Turbonomic, Movere]\n\n"
            "Follow exact JSON structure of existing domain files in context/creative/domains/."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "mce-fnd-02",
        "title": "MCE: Register migration domain in tools/creative/competitor_discoverer.py",
        "description": (
            "File: tools/creative/competitor_discoverer.py\n\n"
            "Find where domain configs are loaded (search for 'domains/' or 'domain_config').\n"
            "Ensure the migration domain JSON from mce-fnd-01 is picked up automatically.\n"
            "If domains are explicitly enumerated, add 'migration' to the list.\n"
            "Verify with: python -m tools.creative.creative_engine --discover --domain migration --json\n"
            "Expected output: shows migration competitors seeded from competitor_seeds list."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-fnd-01",
    },
    {
        "id": "mce-fnd-03",
        "title": "MCE: Run icdev-maintain scan — capture top-10 code quality findings",
        "description": (
            "Run the code intelligence codelens scan and document findings:\n\n"
            "  python -m tools.code_intelligence.codelens --all --json > /tmp/codelens_out.json\n\n"
            "From output, extract top-10 files by complexity/smell score.\n"
            "Create docs/migration-canvas/innovation-findings.md with:\n"
            "  - Table: file, metric, value, recommended fix\n"
            "  - Section per finding type (cyclomatic complexity, nesting, param count)\n"
            "  - CUI // SP-CTI header\n\n"
            "Do NOT fix anything yet — discovery only. Fixes are in mce-inn-01."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-fnd-02",
    },
    {
        "id": "mce-fnd-04",
        "title": "MCE: Document 26 CLI harmonization gaps in docs/migration-canvas/cli-gaps.md",
        "description": (
            "The Innovation Engine introspective scan found 26 CLI harmonization gaps.\n"
            "Create docs/migration-canvas/cli-gaps.md with:\n"
            "  - CUI // SP-CTI header\n"
            "  - Table: tool path, current CLI flags, recommended harmonized pattern, priority\n"
            "  - Reference: innovation pipeline_id=pipe-d90684be, introspect.cli_harmonization signals\n\n"
            "Query: python -c \"\n"
            "from tools.db.storage import get_connection\n"
            "conn = get_connection()\n"
            "rows = conn.execute(\\\"SELECT title, source, metadata FROM innovation_signals\n"
            "  WHERE source='introspective' AND title LIKE '%cli%'\n"
            "  ORDER BY created_at DESC LIMIT 30\\\").fetchall()\n"
            "for r in rows: print(dict(r))\n"
            "\"\n\n"
            "Document the 5 highest-priority gaps with concrete harmonization steps."
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-fnd-03",
    },
    {
        "id": "mce-fnd-vv",
        "title": "MCE: V&V Gate — Phase 1 Foundation",
        "description": (
            "Phase 1 verification gate. Run in order:\n\n"
            "1. CodeLens: python -m tools.code_intelligence.codelens --all --json\n"
            "   Pass: no new critical smells vs baseline.\n\n"
            "2. Coherence: python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "   Pass: gate exits 0.\n\n"
            "3. Verify creative domain registered:\n"
            "   python -m tools.creative.creative_engine --status --json\n"
            "   Pass: output shows migration in active domains or competitor list.\n\n"
            "4. Playwright screenshot:\n"
            "   python -m playwright screenshot chromium http://localhost:5050/migration-canvas/server-migration/new\n"
            "   playwright/screenshots/mce-fnd-vv.png\n"
            "   Pass: page loads, no 500 error, wizard step 1 visible.\n\n"
            "5. Companion sync: python tools/dx/companion.py --sync --write --json\n\n"
            "Mark done only when all 5 pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-fnd-04",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # EP2: COMPLIANCE GATE — FedRAMP/DISA/IL check at Migration Type step
    # Starts after Phase 1 V&V
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "mce-cg-01",
        "title": "MCE: Add mc_compliance_checks table to tools/migration_canvas/db/init_db.py",
        "description": (
            "File: tools/migration_canvas/db/init_db.py\n\n"
            "Append to the schema string (idempotent CREATE TABLE IF NOT EXISTS):\n\n"
            "mc_compliance_checks(\n"
            "  id TEXT PRIMARY KEY,\n"
            "  session_id TEXT NOT NULL REFERENCES mc_sessions(id),\n"
            "  migration_type TEXT NOT NULL,\n"
            "  target_env TEXT NOT NULL,\n"
            "  il_level TEXT CHECK(il_level IN ('IL2','IL4','IL5','IL6','Unknown')),\n"
            "  framework TEXT NOT NULL,\n"
            "  result TEXT NOT NULL CHECK(result IN ('pass','warn','block')),\n"
            "  findings_json TEXT DEFAULT '[]',\n"
            "  checked_at TEXT NOT NULL,\n"
            "  classification TEXT DEFAULT 'CUI'\n"
            ") WITHOUT ROWID;\n"
            "CREATE INDEX IF NOT EXISTS idx_mc_compliance_session ON mc_compliance_checks(session_id);\n\n"
            "No other changes. Run migration_canvas DB init to verify table created."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-fnd-vv",
    },
    {
        "id": "mce-cg-02",
        "title": "MCE: Create tools/migration_canvas/compliance_gate.py",
        "description": (
            "New module: tools/migration_canvas/compliance_gate.py\n\n"
            "Implement check_migration_compliance(migration_type, target_env, il_level) -> dict:\n"
            "  Returns {framework, result, findings: [{control, severity, message}], checked_at}\n\n"
            "Frameworks to check (read from MC_COMPLIANCE_RULES in constants.py):\n"
            "  FedRAMP: required if target_env in (AWS GovCloud, Azure Gov, GCP Gov)\n"
            "  DISA STIG: required if il_level in (IL4, IL5, IL6)\n"
            "  DISA IL4/IL5: required if il_level in (IL4, IL5, IL6)\n"
            "  NIST 800-53: always applied\n\n"
            "Logic:\n"
            "  P2C to non-FedRAMP cloud without IL declaration → result=warn\n"
            "  P2C to GovCloud without FedRAMP ATO declared → result=warn\n"
            "  IL5/IL6 workload to commercial cloud → result=block\n"
            "  Missing IL level for DoD workload → result=warn\n\n"
            "save_compliance_check(session_id, check_result, db_path) — persists to mc_compliance_checks.\n"
            "No Flask imports. Pure functions only."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-cg-01",
    },
    {
        "id": "mce-cg-03",
        "title": "MCE: Add COMPLIANCE_FRAMEWORKS + IL_LEVEL_REQUIREMENTS to constants.py",
        "description": (
            "File: tools/migration_canvas/constants.py\n\n"
            "Add two new constants after existing ones:\n\n"
            "COMPLIANCE_FRAMEWORKS = {\n"
            "  'fedramp': {'label': 'FedRAMP', 'applies_to_envs': ['AWS GovCloud', 'Azure Government', 'GCP Gov'], 'url': 'https://fedramp.gov'},\n"
            "  'disa_stig': {'label': 'DISA STIG', 'applies_to_ils': ['IL4','IL5','IL6']},\n"
            "  'nist_800_53': {'label': 'NIST 800-53', 'applies_to_ils': ['IL2','IL4','IL5','IL6']},\n"
            "  'cmmc2': {'label': 'CMMC 2.0', 'applies_to_envs': ['On-Prem','Hybrid']},\n"
            "}\n\n"
            "IL_LEVEL_REQUIREMENTS = {\n"
            "  'IL2': {'max_classification': 'Unclassified', 'commercial_ok': True},\n"
            "  'IL4': {'max_classification': 'CUI', 'commercial_ok': False, 'requires_govcloud': True},\n"
            "  'IL5': {'max_classification': 'CUI-High', 'commercial_ok': False, 'requires_govcloud': True, 'requires_disa_stig': True},\n"
            "  'IL6': {'max_classification': 'SECRET', 'commercial_ok': False, 'requires_sipr': True},\n"
            "}"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-cg-01",
    },
    {
        "id": "mce-cg-04",
        "title": "MCE: Wire /migration-canvas/server-migration/compliance-check route in blueprint.py",
        "description": (
            "File: tools/migration_canvas/blueprint.py\n\n"
            "Add inside create_migration_blueprint() before return bp:\n\n"
            "  @bp.route('/server-migration/compliance-check', methods=['POST'])\n"
            "  @mc_login_required\n"
            "  def mc_compliance_check():\n"
            "    data = request.get_json(force=True)\n"
            "    migration_type = data.get('migration_type', '')\n"
            "    target_env = data.get('target_env', '')\n"
            "    il_level = data.get('il_level', 'Unknown')\n"
            "    session_id = data.get('session_id')\n"
            "    from tools.migration_canvas.compliance_gate import check_migration_compliance, save_compliance_check\n"
            "    result = check_migration_compliance(migration_type, target_env, il_level)\n"
            "    if session_id:\n"
            "        save_compliance_check(session_id, result, db_path=_DB_PATH)\n"
            "    return jsonify(result)\n\n"
            "Import compliance_gate lazily inside the route (avoids circular import).\n"
            "No template changes in this task — UI is mce-cg-05."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-cg-02",
    },
    {
        "id": "mce-cg-05",
        "title": "MCE: Update server_wizard.html Step 1 — compliance warning/block UI",
        "description": (
            "File: tools/dashboard/templates/migration_canvas/server_wizard.html\n\n"
            "In Step 1 (Migration Type & Target):\n"
            "1. After Migration Type card selection, add IL Level dropdown:\n"
            "   <select id='il-level'> IL2 / IL4 / IL5 / IL6 / Unknown </select>\n\n"
            "2. On migration type or IL level change, call:\n"
            "   fetch('/migration-canvas/server-migration/compliance-check', {method:'POST', body:...})\n"
            "   Display result inline as a compliance badge strip:\n"
            "   - pass → green badge per framework (NIST 800-53 ✓, FedRAMP ✓)\n"
            "   - warn → yellow badge + expandable findings list\n"
            "   - block → red banner + 'This migration cannot proceed without resolving:' list\n"
            "   - block result: disable the Proceed button\n\n"
            "3. Store il_level in the session JS object so downstream steps can read it.\n\n"
            "No backend changes. Pure JS + Jinja. Keep existing card layout intact."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-cg-04",
    },
    {
        "id": "mce-cg-06",
        "title": "MCE: Create tests/test_mce_compliance_gate.py",
        "description": (
            "New file: tests/test_mce_compliance_gate.py\n\n"
            "Use pytest. Import check_migration_compliance from tools.migration_canvas.compliance_gate.\n\n"
            "Test cases (minimum 8):\n"
            "  test_il5_to_commercial_cloud_is_blocked()\n"
            "  test_il4_to_govcloud_passes()\n"
            "  test_p2c_to_govcloud_without_fedramp_warns()\n"
            "  test_unknown_il_to_dod_env_warns()\n"
            "  test_il2_to_commercial_passes()\n"
            "  test_nist_80053_always_applied()\n"
            "  test_block_disables_proceed_flag()\n"
            "  test_findings_list_populated_on_warn()\n\n"
            "Run via: python -m pytest tests/test_mce_compliance_gate.py -v\n"
            "All 8 must pass before mce-cg-vv."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-cg-02",
    },
    {
        "id": "mce-cg-vv",
        "title": "MCE: V&V Gate — Phase 2 Compliance Gate",
        "description": (
            "Phase 2 verification gate. Run in order:\n\n"
            "1. Unit tests: python -m pytest tests/test_mce_compliance_gate.py -v\n"
            "   Pass: all 8 tests green.\n\n"
            "2. CodeLens: python -m tools.code_intelligence.codelens --project-dir tools/migration_canvas --json\n"
            "   Pass: compliance_gate.py scores < 10 cyclomatic complexity.\n\n"
            "3. Coherence: python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "4. Playwright — navigate to wizard, select P2C migration type, set IL5:\n"
            "   Verify red block banner appears. Take screenshot:\n"
            "   playwright/screenshots/mce-cg-vv-block.png\n"
            "   Then select IL4 + AWS GovCloud: verify yellow warn badge.\n"
            "   playwright/screenshots/mce-cg-vv-warn.png\n\n"
            "5. Companion sync: python tools/dx/companion.py --sync --write --json\n\n"
            "Mark done only when all 5 pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-cg-06",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # EP3: INVENTORY DISCOVERY — CSV/JSON import + SSH probe skeleton
    # Parallel to EP2 — both start after fnd-vv
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "mce-inv-01",
        "title": "MCE: Add mc_inventory_imports table to tools/migration_canvas/db/init_db.py",
        "description": (
            "File: tools/migration_canvas/db/init_db.py\n\n"
            "Append (idempotent CREATE TABLE IF NOT EXISTS):\n\n"
            "mc_inventory_imports(\n"
            "  id TEXT PRIMARY KEY,\n"
            "  session_id TEXT NOT NULL,\n"
            "  format TEXT NOT NULL CHECK(format IN ('csv','json','aws_hub','azure_migrate','manual')),\n"
            "  filename TEXT,\n"
            "  row_count INTEGER DEFAULT 0,\n"
            "  parsed_servers_json TEXT DEFAULT '[]',\n"
            "  status TEXT NOT NULL CHECK(status IN ('pending','parsed','error')) DEFAULT 'pending',\n"
            "  error_msg TEXT,\n"
            "  imported_at TEXT NOT NULL,\n"
            "  classification TEXT DEFAULT 'CUI'\n"
            ") WITHOUT ROWID;\n"
            "CREATE INDEX IF NOT EXISTS idx_mc_inv_session ON mc_inventory_imports(session_id);\n\n"
            "No other changes."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-fnd-vv",
    },
    {
        "id": "mce-inv-02",
        "title": "MCE: Create tools/migration_canvas/inventory_scanner.py",
        "description": (
            "New file: tools/migration_canvas/inventory_scanner.py\n\n"
            "Implement:\n\n"
            "parse_csv_import(file_bytes, filename) -> {servers: [...], row_count, errors: []}\n"
            "  Accepts generic CSV: columns hostname, ip, os, cpu_cores, ram_gb, disk_gb, role\n"
            "  Also accepts AWS Migration Hub export column names (auto-detected)\n\n"
            "parse_json_import(file_bytes) -> {servers: [...], row_count, errors: []}\n"
            "  Accepts: Azure Migrate JSON, generic {servers:[]} envelope, flat list\n\n"
            "Server record schema (all fields optional except hostname):\n"
            "  {id, hostname, ip, os, cpu_cores, ram_gb, disk_gb, role, tags, source_format}\n\n"
            "save_import(session_id, parsed_result, format, filename, db_path) -> import_id\n"
            "  Writes to mc_inventory_imports.\n\n"
            "No Flask, no LLM, no network calls. Pure parsing only.\n"
            "Max 200 LOC."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inv-01",
    },
    {
        "id": "mce-inv-03",
        "title": "MCE: Wire inventory import route in tools/migration_canvas/blueprint.py",
        "description": (
            "File: tools/migration_canvas/blueprint.py\n\n"
            "Add inside create_migration_blueprint():\n\n"
            "  @bp.route('/server-migration/<session_id>/inventory/import', methods=['GET'])\n"
            "  @mc_login_required\n"
            "  def mc_inventory_import_page(session_id): -> render inventory_import.html\n\n"
            "  @bp.route('/server-migration/<session_id>/inventory/import', methods=['POST'])\n"
            "  @mc_login_required\n"
            "  def mc_inventory_import_upload(session_id):\n"
            "    file = request.files.get('inventory_file')\n"
            "    fmt = request.form.get('format', 'csv')\n"
            "    from tools.migration_canvas.inventory_scanner import parse_csv_import, parse_json_import, save_import\n"
            "    parser = parse_csv_import if fmt == 'csv' else parse_json_import\n"
            "    result = parser(file.read(), file.filename)\n"
            "    import_id = save_import(session_id, result, fmt, file.filename, _DB_PATH)\n"
            "    return jsonify({'import_id': import_id, 'row_count': result['row_count'], 'errors': result['errors']})\n\n"
            "Lazy imports inside routes. 16MB file size limit (use app.config or check len)."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inv-02",
    },
    {
        "id": "mce-inv-04",
        "title": "MCE: Create tools/dashboard/templates/migration_canvas/inventory_import.html",
        "description": (
            "New template: tools/dashboard/templates/migration_canvas/inventory_import.html\n\n"
            "Extends base.html. Dark theme matching server_wizard.html (#0d1117 bg).\n\n"
            "Layout:\n"
            "  1. Format selector tabs: CSV | JSON | AWS Migration Hub | Azure Migrate\n"
            "  2. Drag-and-drop file upload zone with fallback click-to-browse\n"
            "  3. Column mapping preview table (auto-detected columns → ICDEV fields)\n"
            "  4. Import button → POST to import route → progress indicator\n"
            "  5. Results panel: row count, errors list, parsed server table preview (first 20 rows)\n"
            "  6. 'Populate Inventory Step' button → populates step 2 of server_wizard.html via sessionStorage\n\n"
            "Uses fetch() for upload. No page reload.\n"
            "Also mirror to icdev/tools/dashboard/templates/migration_canvas/inventory_import.html."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inv-03",
    },
    {
        "id": "mce-inv-05",
        "title": "MCE: Create tests/test_mce_inventory_scanner.py",
        "description": (
            "New file: tests/test_mce_inventory_scanner.py\n\n"
            "Use pytest. Test parse_csv_import and parse_json_import.\n\n"
            "Test cases (minimum 6):\n"
            "  test_parse_generic_csv_5_servers()\n"
            "  test_parse_aws_migration_hub_csv()\n"
            "  test_parse_generic_json_envelope()\n"
            "  test_parse_azure_migrate_json()\n"
            "  test_empty_file_returns_zero_servers()\n"
            "  test_malformed_csv_returns_errors_not_exception()\n\n"
            "Use inline fixture strings (no external files).\n"
            "Run: python -m pytest tests/test_mce_inventory_scanner.py -v"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inv-02",
    },
    {
        "id": "mce-inv-vv",
        "title": "MCE: V&V Gate — Phase 3 Inventory Discovery",
        "description": (
            "Phase 3 verification gate. Run in order:\n\n"
            "1. Unit tests: python -m pytest tests/test_mce_inventory_scanner.py -v\n"
            "   Pass: all 6 tests green.\n\n"
            "2. CodeLens: python -m tools.code_intelligence.codelens --project-dir tools/migration_canvas --json\n\n"
            "3. Coherence: python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "4. Playwright — navigate to inventory import page:\n"
            "   http://localhost:5050/migration-canvas/server-migration/test-session/inventory/import\n"
            "   Verify page loads, upload zone renders, format tabs visible.\n"
            "   playwright/screenshots/mce-inv-vv.png\n\n"
            "5. Companion sync: python tools/dx/companion.py --sync --write --json\n\n"
            "Mark done only when all 5 pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inv-05",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # EP4: WAVE PLANNER — dependency graph + wave assignment
    # Starts after EP3 V&V (needs inventory data model)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "mce-wp-01",
        "title": "MCE: Add mc_migration_waves + mc_server_dependencies tables to init_db.py",
        "description": (
            "File: tools/migration_canvas/db/init_db.py\n\n"
            "Append two new table DDLs (idempotent):\n\n"
            "mc_migration_waves(\n"
            "  id TEXT PRIMARY KEY,\n"
            "  session_id TEXT NOT NULL,\n"
            "  wave_number INTEGER NOT NULL,\n"
            "  name TEXT NOT NULL,\n"
            "  cutover_date TEXT,\n"
            "  status TEXT CHECK(status IN ('planned','in_progress','complete','blocked')) DEFAULT 'planned',\n"
            "  server_ids_json TEXT DEFAULT '[]',\n"
            "  notes TEXT,\n"
            "  created_at TEXT NOT NULL,\n"
            "  classification TEXT DEFAULT 'CUI'\n"
            ");\n"
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_waves_num ON mc_migration_waves(session_id, wave_number);\n\n"
            "mc_server_dependencies(\n"
            "  id TEXT PRIMARY KEY,\n"
            "  session_id TEXT NOT NULL,\n"
            "  source_server_id TEXT NOT NULL,\n"
            "  target_server_id TEXT NOT NULL,\n"
            "  dep_type TEXT CHECK(dep_type IN ('network','application','database','auth','storage')) DEFAULT 'network',\n"
            "  direction TEXT CHECK(direction IN ('inbound','outbound','bidirectional')) DEFAULT 'bidirectional',\n"
            "  notes TEXT,\n"
            "  created_at TEXT NOT NULL\n"
            ");\n"
            "CREATE INDEX IF NOT EXISTS idx_mc_deps_session ON mc_server_dependencies(session_id);"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inv-vv",
    },
    {
        "id": "mce-wp-02",
        "title": "MCE: Create tools/migration_canvas/wave_planner.py",
        "description": (
            "New file: tools/migration_canvas/wave_planner.py\n\n"
            "Implement:\n\n"
            "build_dependency_graph(session_id, db_path) -> {nodes: [{id, hostname, wave}], edges: [{src, tgt, dep_type}]}\n"
            "  Reads mc_server_dependencies + mc_inventory_imports for the session.\n"
            "  Returns Sigma.js-compatible graph JSON.\n\n"
            "auto_assign_waves(session_id, db_path) -> {waves: [{wave_number, server_ids, reason}]}\n"
            "  Topological sort of dependency graph → assign servers to waves.\n"
            "  Servers with no dependencies → Wave 1.\n"
            "  Servers dependent on Wave N → Wave N+1.\n"
            "  Returns proposed wave assignments (does not write to DB).\n\n"
            "save_wave(session_id, wave_number, name, server_ids, cutover_date, db_path) -> wave_id\n"
            "save_dependency(session_id, src_id, tgt_id, dep_type, direction, db_path) -> dep_id\n"
            "get_waves(session_id, db_path) -> [wave dicts]\n\n"
            "No Flask. Pure functions. Max 250 LOC."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-wp-01",
    },
    {
        "id": "mce-wp-03",
        "title": "MCE: Add WAVE_STATUS + DEPENDENCY_TYPES constants to constants.py",
        "description": (
            "File: tools/migration_canvas/constants.py\n\n"
            "Add:\n\n"
            "WAVE_STATUS = {\n"
            "  'planned': {'label': 'Planned', 'color': '#6c757d'},\n"
            "  'in_progress': {'label': 'In Progress', 'color': '#fd7e14'},\n"
            "  'complete': {'label': 'Complete', 'color': '#28a745'},\n"
            "  'blocked': {'label': 'Blocked', 'color': '#dc3545'},\n"
            "}\n\n"
            "DEPENDENCY_TYPES = {\n"
            "  'network': {'label': 'Network', 'color': '#17a2b8', 'icon': '🌐'},\n"
            "  'application': {'label': 'Application', 'color': '#6f42c1', 'icon': '📦'},\n"
            "  'database': {'label': 'Database', 'color': '#e83e8c', 'icon': '🗄'},\n"
            "  'auth': {'label': 'Auth/Identity', 'color': '#fd7e14', 'icon': '🔑'},\n"
            "  'storage': {'label': 'Storage', 'color': '#20c997', 'icon': '💾'},\n"
            "}"
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-wp-01",
    },
    {
        "id": "mce-wp-04",
        "title": "MCE: Wire wave planner routes in tools/migration_canvas/blueprint.py",
        "description": (
            "File: tools/migration_canvas/blueprint.py\n\n"
            "Add 4 routes inside create_migration_blueprint():\n\n"
            "  GET  /server-migration/<session_id>/waves → render wave_planner.html\n"
            "  GET  /api/server-migration/<session_id>/waves/graph → build_dependency_graph() JSON\n"
            "  POST /api/server-migration/<session_id>/waves/auto-assign → auto_assign_waves() JSON\n"
            "  POST /api/server-migration/<session_id>/waves → save_wave() JSON\n"
            "  POST /api/server-migration/<session_id>/dependencies → save_dependency() JSON\n\n"
            "All @mc_login_required. Lazy imports from wave_planner.\n"
            "Also add 'Wave Planner' nav link to the wizard sidebar step 4 area."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-wp-02",
    },
    {
        "id": "mce-wp-05",
        "title": "MCE: Create tools/dashboard/templates/migration_canvas/wave_planner.html",
        "description": (
            "New template: tools/dashboard/templates/migration_canvas/wave_planner.html\n\n"
            "Extends base.html. Split layout:\n"
            "  LEFT (40%): Wave list panel\n"
            "    - Wave cards (Wave 1, Wave 2...) with status badge, server count, cutover date\n"
            "    - 'Auto-Assign Waves' button → calls /waves/auto-assign, populates cards\n"
            "    - Add wave button, drag servers between waves\n"
            "  RIGHT (60%): Sigma.js dependency graph\n"
            "    - Nodes = servers (color by wave assignment)\n"
            "    - Edges = dependencies (color by dep_type from DEPENDENCY_TYPES)\n"
            "    - Click node to select server and assign to wave\n"
            "    - Click edge to see dep type label\n"
            "    - 'Add Dependency' button → modal with src/tgt server pickers + dep_type dropdown\n\n"
            "Load Sigma.js from CDN (same version used in system graph page).\n"
            "Mirror to icdev/tools/dashboard/templates/migration_canvas/wave_planner.html."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-wp-04",
    },
    {
        "id": "mce-wp-06",
        "title": "MCE: Create tests/test_mce_wave_planner.py",
        "description": (
            "New file: tests/test_mce_wave_planner.py\n\n"
            "Use pytest. Import from tools.migration_canvas.wave_planner.\n"
            "Use in-memory SQLite (not icdev.db) for test isolation.\n\n"
            "Test cases (minimum 6):\n"
            "  test_build_graph_empty_session()\n"
            "  test_build_graph_with_3_servers_2_deps()\n"
            "  test_auto_assign_no_deps_all_wave1()\n"
            "  test_auto_assign_chain_creates_waves()\n"
            "  test_save_wave_creates_db_row()\n"
            "  test_topological_sort_detects_cycle()\n\n"
            "Run: python -m pytest tests/test_mce_wave_planner.py -v"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-wp-02",
    },
    {
        "id": "mce-wp-vv",
        "title": "MCE: V&V Gate — Phase 4 Wave Planner",
        "description": (
            "Phase 4 verification gate. Run in order:\n\n"
            "1. Unit tests: python -m pytest tests/test_mce_wave_planner.py -v\n"
            "   Pass: all 6 tests green.\n\n"
            "2. CodeLens: python -m tools.code_intelligence.codelens --project-dir tools/migration_canvas --json\n\n"
            "3. Coherence: python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "4. Playwright — navigate to wave planner:\n"
            "   http://localhost:5050/migration-canvas/server-migration/test-session/waves\n"
            "   Verify Sigma.js graph renders (even empty), wave panel visible.\n"
            "   Click 'Auto-Assign Waves' → verify API call returns JSON without 500.\n"
            "   playwright/screenshots/mce-wp-vv.png\n\n"
            "5. Companion sync: python tools/dx/companion.py --sync --write --json\n\n"
            "Mark done only when all 5 pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-wp-06",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # EP5: DOSSIER GUIDANCE — Research callouts inline in wizard
    # Starts after EP2 V&V (compliance context needed)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "mce-dg-01",
        "title": "MCE: Create tools/migration_canvas/dossier_advisor.py",
        "description": (
            "New file: tools/migration_canvas/dossier_advisor.py\n\n"
            "Reads from the Research Engine DB (research_challenges, research_dossiers tables).\n"
            "Target dossier: rdoss-903cbd6858e5 (Server Migration Analysis, session rsess-1e2fb0fe6c96).\n\n"
            "Implement:\n\n"
            "get_guidance_for_step(wizard_step, migration_type=None, top_k=3) -> [{title, description, severity, category}]\n"
            "  wizard_step: 1=MigrationType, 2=Inventory, 3=Performance, 4=TargetSelection,\n"
            "               5=Compatibility, 6=NICStorage, 7=CutoverPlanner\n"
            "  Maps step to challenge categories:\n"
            "    step 1: compliance, security\n"
            "    step 2: discovery, inventory, automation\n"
            "    step 3: performance, scalability\n"
            "    step 4: ux, integration, api\n"
            "    step 5: compatibility, support\n"
            "    step 6: storage, network\n"
            "    step 7: cutover, rollback, automation\n"
            "  Returns top_k challenges by composite_score for the mapped categories.\n"
            "  Falls back to empty list gracefully if research DB unavailable.\n\n"
            "No Flask. Uses get_connection() from tools.db.storage."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-cg-vv",
    },
    {
        "id": "mce-dg-02",
        "title": "MCE: Wire /migration-canvas/server-migration/guidance API route in blueprint.py",
        "description": (
            "File: tools/migration_canvas/blueprint.py\n\n"
            "Add inside create_migration_blueprint():\n\n"
            "  @bp.route('/server-migration/guidance', methods=['GET'])\n"
            "  @mc_login_required\n"
            "  def mc_guidance():\n"
            "    step = request.args.get('step', 1, type=int)\n"
            "    migration_type = request.args.get('type', '')\n"
            "    from tools.migration_canvas.dossier_advisor import get_guidance_for_step\n"
            "    guidance = get_guidance_for_step(step, migration_type, top_k=3)\n"
            "    return jsonify({'step': step, 'guidance': guidance, 'dossier_id': 'rdoss-903cbd6858e5'})\n\n"
            "No template changes here — UI is mce-dg-03."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-dg-01",
    },
    {
        "id": "mce-dg-03",
        "title": "MCE: Update server_wizard.html — inline dossier guidance callouts per step",
        "description": (
            "File: tools/dashboard/templates/migration_canvas/server_wizard.html\n\n"
            "On each wizard step transition (existing JS step change handler):\n"
            "  1. Call: GET /migration-canvas/server-migration/guidance?step=N&type=MIGRATION_TYPE\n"
            "  2. Render result as a collapsible callout panel at bottom of each step:\n"
            "     Title: 'Research Intelligence — Common Issues at This Step'\n"
            "     Body: list of up to 3 challenge cards:\n"
            "       [severity badge] Challenge title\n"
            "       Short description (first 120 chars)\n"
            "       Source: 'Research Engine — Application & Cloud Migration Dossier'\n"
            "  3. Panel is collapsed by default, click to expand.\n"
            "  4. If guidance array is empty, hide the panel entirely.\n\n"
            "No backend changes. Append JS to existing step-change handler.\n"
            "Badge colors: critical=red, high=orange, medium=yellow, low=gray."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-dg-02",
    },
    {
        "id": "mce-dg-04",
        "title": "MCE: Create tests/test_mce_dossier_advisor.py",
        "description": (
            "New file: tests/test_mce_dossier_advisor.py\n\n"
            "Use pytest. Import get_guidance_for_step from tools.migration_canvas.dossier_advisor.\n\n"
            "Test cases (minimum 4):\n"
            "  test_step1_returns_compliance_challenges()\n"
            "  test_step7_returns_cutover_challenges()\n"
            "  test_top_k_respected()\n"
            "  test_graceful_fallback_if_no_research_data()\n\n"
            "Mock get_connection() to return in-memory DB with sample research_challenges rows.\n"
            "Run: python -m pytest tests/test_mce_dossier_advisor.py -v"
        ),
        "task_type": "test",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-dg-01",
    },
    {
        "id": "mce-dg-vv",
        "title": "MCE: V&V Gate — Phase 5 Dossier Guidance",
        "description": (
            "Phase 5 verification gate. Run in order:\n\n"
            "1. Unit tests: python -m pytest tests/test_mce_dossier_advisor.py -v\n"
            "   Pass: all 4 tests green.\n\n"
            "2. CodeLens: python -m tools.code_intelligence.codelens --project-dir tools/migration_canvas --json\n\n"
            "3. Coherence: python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "4. Playwright — navigate to new migration wizard, advance to Step 2:\n"
            "   http://localhost:5050/migration-canvas/server-migration/new\n"
            "   Dismiss tour modal, select P2V migration type, click Proceed.\n"
            "   Verify 'Research Intelligence' callout panel appears at bottom of Step 2.\n"
            "   playwright/screenshots/mce-dg-vv-step2-guidance.png\n\n"
            "5. Companion sync: python tools/dx/companion.py --sync --write --json\n\n"
            "Mark done only when all 5 pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-dg-04",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # EP6: INNOVATION FOLLOW-THROUGH — code quality + CLI harmonization
    # Starts after EP1 V&V, parallel to EP2/EP3
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "mce-inn-01",
        "title": "MCE: Fix top-10 code quality issues from innovation codelens scan",
        "description": (
            "Reference: docs/migration-canvas/innovation-findings.md (created in mce-fnd-03).\n\n"
            "Fix the top-10 files by complexity score. Allowed fixes:\n"
            "  - Extract long functions into smaller helpers (max 30 lines per function)\n"
            "  - Reduce nesting depth (max 4 levels)\n"
            "  - Split functions with > 5 parameters into typed dicts or dataclasses\n\n"
            "Rules:\n"
            "  - One commit per file fixed\n"
            "  - Do NOT change public API signatures\n"
            "  - Do NOT add new features\n"
            "  - Each fix must have before/after complexity score comment in commit message\n\n"
            "After all fixes: re-run codelens and confirm average complexity dropped.\n"
            "python -m tools.code_intelligence.codelens --all --json"
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-fnd-vv",
    },
    {
        "id": "mce-inn-02",
        "title": "MCE: Harmonize CLI interfaces for top-5 tools from CLI gaps doc",
        "description": (
            "Reference: docs/migration-canvas/cli-gaps.md (created in mce-fnd-04).\n\n"
            "Apply the harmonized flag pattern to the top-5 priority tools listed in the doc.\n"
            "Standard ICDEV CLI convention:\n"
            "  --json (machine output), --human (human output), --db-path (override)\n"
            "  --run (full pipeline), --status (health check), --json flag always last\n\n"
            "For each tool:\n"
            "  1. Add missing flags with argparse (backward-compatible — old flags still work)\n"
            "  2. Update tools/manifest/<topic>.md shard entry with new CLI examples\n"
            "  3. Test: python -m <tool> --help shows new flags\n\n"
            "Do NOT break existing callers. Additive changes only."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inn-01",
    },
    {
        "id": "mce-inn-03",
        "title": "MCE: Add deprecation markers to 17 unused tools from innovation scan",
        "description": (
            "The Innovation Engine identified 17 unused tools (introspective.unused_tools signals).\n\n"
            "Query them:\n"
            "  python -c \"\n"
            "  from tools.db.storage import get_connection\n"
            "  conn = get_connection()\n"
            "  rows = conn.execute(\\\"SELECT title, metadata FROM innovation_signals\n"
            "    WHERE source='introspective' AND title LIKE '%unused%'\n"
            "    ORDER BY created_at DESC LIMIT 20\\\").fetchall()\n"
            "  for r in rows: print(dict(r))\n"
            "  \"\n\n"
            "For each genuinely unused tool (verify with grep for callers):\n"
            "  - Add docstring: '# DEPRECATED: unused as of 2026-05-09. Remove after 2026-08-01.'\n"
            "  - Add entry to tools/manifest/<topic>.md: mark [DEPRECATED]\n\n"
            "Do NOT delete files yet — deprecation markers only.\n"
            "Exception: if a tool has zero callers AND is < 50 LOC, may delete with confirmation."
        ),
        "task_type": "chore",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-inn-02",
    },
    {
        "id": "mce-inn-vv",
        "title": "MCE: Final V&V Gate — All Phases Complete",
        "description": (
            "Final cross-phase verification. Runs after all 6 epics are done.\n\n"
            "1. Full test suite:\n"
            "   python -m pytest tests/test_mce_compliance_gate.py tests/test_mce_inventory_scanner.py\n"
            "               tests/test_mce_wave_planner.py tests/test_mce_dossier_advisor.py -v\n"
            "   Pass: all 24 tests green.\n\n"
            "2. CodeLens full scan: python -m tools.code_intelligence.codelens --all --json\n"
            "   Pass: no new critical smells vs pre-project baseline.\n\n"
            "3. Coherence: python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "   Pass: exits 0.\n\n"
            "4. Playwright full migration canvas smoke test:\n"
            "   - Load /migration-canvas/server-migration/new\n"
            "   - Select P2C migration, set IL5 → verify compliance block\n"
            "   - Navigate to inventory import page → verify upload UI\n"
            "   - Navigate to wave planner → verify Sigma.js graph\n"
            "   - Advance wizard step → verify guidance callout\n"
            "   playwright/screenshots/mce-final-vv.png\n\n"
            "5. Companion sync: python tools/dx/companion.py --sync --write --json\n"
            "6. Create docs/features/phase-mce-migration-canvas-enhancements.md\n\n"
            "Mark done only when all 6 pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "mce-dg-vv",
    },
]


def seed():
    report = create_tasks("mce", TASKS, strict=False, register_project=False)
    print(report.summary())
    print(f"\nDone — {len(report.seeded)} inserted, {len(report.skipped)} skipped.")
    print(f"Entry point 'mce-fnd-01' is status=scheduled, scheduled_at={_NOW}")
    print("Run genesis kanban scheduler to pick up: python -m tools.genesis.kanban_scheduler --once")


if __name__ == "__main__":
    seed()
