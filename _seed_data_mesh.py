from __future__ import annotations
# CUI // SP-CTI — Seed Data Mesh (dm-*) Kanban tasks
"""Seed kanban_tasks for the Data Mesh integration project (dm-*).

Six new pages at /data/: domains, products, contracts, governance, csp, mesh.
Covers all four Data Mesh pillars using open source tools + CSP native services:
  - OpenMetadata (catalog), OpenLineage + Marquez (lineage standard)
  - Great Expectations + dbt-core (quality), datacontract-cli ODCS (contracts)
  - OPA (federated governance), pyiceberg (table format)
  - AWS DataZone / Azure Purview / GCP Dataplex (CSP bridges)
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "icdev.db"
NOW = datetime.now(timezone.utc).isoformat()
SCHED = NOW


def seed():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    tasks = [
        # ══════════════════════════════════════════════════════════════════════
        # Epic 1: Foundation — DB tables + constants + data_mesh/ package
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-found-01",
            "title": "Data Mesh: Add 9 dm_* tables to tools/data_canvas/db/init_db.py",
            "description": (
                "Append the following tables to SCHEMA in tools/data_canvas/db/init_db.py.\n"
                "All use CREATE TABLE IF NOT EXISTS with covering indexes.\n\n"
                "dm_domains (id TEXT PK, name TEXT NOT NULL, description TEXT, owner_team TEXT,\n"
                "  owner_email TEXT, maturity_level TEXT DEFAULT 'defined', classification TEXT\n"
                "  DEFAULT 'CUI // SP-CTI', created_at TEXT, updated_at TEXT)\n\n"
                "dm_data_products (id TEXT PK, domain_id TEXT REFERENCES dm_domains(id),\n"
                "  name TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'draft',\n"
                "  output_port_type TEXT, sla_tier TEXT DEFAULT 'standard', owner_team TEXT,\n"
                "  classification TEXT DEFAULT 'CUI // SP-CTI', created_at TEXT, updated_at TEXT)\n\n"
                "dm_product_slas (id TEXT PK, product_id TEXT REFERENCES dm_data_products(id)\n"
                "  ON DELETE CASCADE, sla_type TEXT, target_value REAL, unit TEXT, created_at TEXT)\n\n"
                "dm_product_subscriptions (id TEXT PK, product_id TEXT REFERENCES dm_data_products(id),\n"
                "  subscriber_team TEXT, purpose TEXT, approved INTEGER DEFAULT 0, created_at TEXT)\n\n"
                "dm_data_contracts (id TEXT PK, domain_id TEXT, product_id TEXT, name TEXT NOT NULL,\n"
                "  contract_yaml TEXT, version TEXT DEFAULT '1.0.0', status TEXT DEFAULT 'draft',\n"
                "  classification TEXT DEFAULT 'CUI // SP-CTI', created_at TEXT, updated_at TEXT)\n\n"
                "dm_contract_test_runs (id TEXT PK, contract_id TEXT REFERENCES dm_data_contracts(id)\n"
                "  ON DELETE CASCADE, passed INTEGER, error_count INTEGER DEFAULT 0,\n"
                "  warnings INTEGER DEFAULT 0, result_json TEXT DEFAULT '{}', created_at TEXT)\n\n"
                "dm_opa_policies (id TEXT PK, domain_id TEXT, name TEXT NOT NULL, rego_text TEXT,\n"
                "  policy_path TEXT, enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)\n\n"
                "dm_policy_audit_log (id TEXT PK, policy_id TEXT, user TEXT, resource TEXT,\n"
                "  decision INTEGER, reason TEXT, classification TEXT DEFAULT 'CUI // SP-CTI',\n"
                "  created_at TEXT)\n\n"
                "dm_csp_sync_log (id TEXT PK, provider TEXT, domain_id TEXT, product_id TEXT,\n"
                "  operation TEXT, status TEXT, synced_count INTEGER DEFAULT 0,\n"
                "  error_detail TEXT DEFAULT '', created_at TEXT)\n\n"
                "Add indexes: idx_dm_products_domain on dm_data_products(domain_id),\n"
                "idx_dm_contracts_domain on dm_data_contracts(domain_id),\n"
                "idx_dm_policies_domain on dm_opa_policies(domain_id),\n"
                "idx_dm_csp_provider on dm_csp_sync_log(provider, created_at)."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": None,
        },
        {
            "id": "dm-found-02",
            "title": "Data Mesh: Add DM_ constants to constants.py + create data_mesh/ package",
            "description": (
                "1. In tools/data_canvas/constants.py add Data Mesh constant blocks:\n"
                "   DM_DOMAIN_MATURITY_LEVELS = ['defined', 'managed', 'optimizing']\n"
                "   DM_PRODUCT_STATUS = ['draft', 'published', 'deprecated']\n"
                "   DM_OUTPUT_PORT_TYPES = ['table', 'api', 'stream', 'file', 'lakehouse']\n"
                "   DM_SLA_TIERS = ['bronze', 'silver', 'gold', 'platinum']\n"
                "   DM_CONTRACT_STATUS = ['draft', 'active', 'violated', 'deprecated']\n"
                "   DM_CSP_PROVIDERS = ['aws_datazone', 'azure_purview', 'gcp_dataplex']\n"
                "   DM_GOVERNANCE_SCORE_GATE = 0.6  # fraction of domains needing active policy\n\n"
                "2. Create tools/data_canvas/data_mesh/__init__.py with module docstring:\n"
                "   'Data Mesh integration layer — Domains, Products, Contracts, Governance, CSP.'\n"
                "   No imports yet — backing modules added in subsequent tasks."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-found-01",
        },
        {
            "id": "dm-found-vv",
            "title": "Data Mesh Foundation V&V: import check + DB schema verify",
            "description": (
                "1. python -c \"from tools.data_canvas.constants import DM_DOMAIN_MATURITY_LEVELS; print('OK')\"\n"
                "   Must print OK with no ImportError.\n"
                "2. python -c \"from tools.data_canvas.data_mesh import __doc__; print(__doc__)\"\n"
                "   Must print the module docstring.\n"
                "3. python tools/data_canvas/db/init_db.py\n"
                "   Run init_db to apply new tables. Verify all 9 dm_* tables exist:\n"
                "   python -c \"\n"
                "   import sqlite3; conn=sqlite3.connect('data/data_canvas.db')\n"
                "   tables=[r[0] for r in conn.execute(\\\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'dm_%'\\\").fetchall()]\n"
                "   print(sorted(tables))\n\"\n"
                "   Must show all 9 dm_* tables. Fix any schema syntax errors before proceeding."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dm-found-02",
        },

        # ══════════════════════════════════════════════════════════════════════
        # Epic 2: Data Domain Management — /data/domains
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-domain-01",
            "title": "Data Mesh: Create tools/data_canvas/data_mesh/domain_manager.py",
            "description": (
                "Pure-function domain management module. No Flask, no LLM.\n"
                "Uses tools/data_canvas/db/init_db.py get_connection() (targets data_canvas.db).\n\n"
                "Functions:\n"
                "  list_domains() -> list[dict]  — all rows from dm_domains ordered by name\n"
                "  get_domain(domain_id: str) -> dict | None\n"
                "  create_domain(data: dict) -> dict  — generate uuid id, set created_at/updated_at\n"
                "  update_domain(domain_id: str, data: dict) -> dict | None  — set updated_at\n"
                "  delete_domain(domain_id: str) -> bool  — only if no dm_data_products reference it\n"
                "  compute_domain_maturity(domain_id: str) -> dict:\n"
                "    — product_count: int (dm_data_products WHERE domain_id)\n"
                "    — contract_count: int (dm_data_contracts WHERE domain_id)\n"
                "    — policy_count: int (dm_opa_policies WHERE domain_id AND enabled=1)\n"
                "    — maturity_score: float 0-1 (weighted: products 0.4, contracts 0.3, policies 0.3)\n"
                "    — maturity_label: 'defined' (<0.4), 'managed' (0.4-0.7), 'optimizing' (>=0.7)\n"
                "  list_domain_products(domain_id: str) -> list[dict]  — products in domain\n\n"
                "Never raises — wrap all DB ops in try/except, return error dict on failure."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-found-vv",
        },
        {
            "id": "dm-domain-02",
            "title": "Data Mesh: Add /domains route + 4 API endpoints to blueprint.py",
            "description": (
                "Add inside create_data_canvas_blueprint() before return bp:\n\n"
                "  @bp.route('/domains')\n"
                "  @dc_login_required\n"
                "  def dc_domains(): render_template('data_canvas/domains.html',\n"
                "    domains=list_domains(), maturity_levels=DM_DOMAIN_MATURITY_LEVELS)\n\n"
                "  @bp.route('/api/dm/domains', methods=['GET', 'POST'])\n"
                "  @dc_login_required\n"
                "  def dc_api_domains(): GET->list_domains(), POST->create_domain(request.json)\n\n"
                "  @bp.route('/api/dm/domains/<domain_id>', methods=['GET', 'PUT', 'DELETE'])\n"
                "  @dc_login_required\n"
                "  def dc_api_domain(domain_id): CRUD via get/update/delete_domain()\n\n"
                "  @bp.route('/api/dm/domains/<domain_id>/maturity')\n"
                "  @dc_login_required\n"
                "  def dc_api_domain_maturity(domain_id): return compute_domain_maturity(domain_id)\n\n"
                "Import domain_manager at top of blueprint.py:\n"
                "  from tools.data_canvas.data_mesh.domain_manager import (\n"
                "    list_domains, get_domain, create_domain, update_domain,\n"
                "    delete_domain, compute_domain_maturity\n"
                "  )\n"
                "Also import DM_DOMAIN_MATURITY_LEVELS from constants."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-domain-01",
        },
        {
            "id": "dm-domain-03",
            "title": "Data Mesh: Create domains.html + icdev/ mirror + nav link in index.html",
            "description": (
                "1. Create tools/dashboard/templates/data_canvas/domains.html\n"
                "   Extends base.html. Dark theme (#16213e/#0f3460 header, #ff9800 accent).\n"
                "   Sections:\n"
                "   - Header: 'Data Domains' + 'New Domain' button (opens modal)\n"
                "   - Domain Cards grid: each card shows name, owner_team, maturity badge\n"
                "     (Defined=grey, Managed=blue, Optimizing=green), product count, contract count\n"
                "   - Maturity meter: mini gauge 0-100% per card\n"
                "   - New Domain modal: name, description, owner_team, owner_email, classification\n"
                "   - Click card -> expand inline to show compute_domain_maturity breakdown\n"
                "   All data via fetch() to /api/dm/domains — no page reload on create.\n\n"
                "2. Mirror: copy to icdev/tools/dashboard/templates/data_canvas/domains.html\n\n"
                "3. In tools/dashboard/templates/data_canvas/index.html add 'Data Mesh' section\n"
                "   to the quick-nav bar with links: Domains | Products | Contracts | Governance | CSP | Mesh\n"
                "   Mirror index.html to icdev/."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-domain-02",
        },
        {
            "id": "dm-domain-vv",
            "title": "Data Mesh Domain V&V: coherence + companion sync + Playwright (/data/domains)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate  (must pass)\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright:\n"
                "   a. Navigate http://localhost:5050/data/domains\n"
                "   b. Verify domain cards grid renders (empty state with 'No domains yet' message if empty)\n"
                "   c. Click 'New Domain' button — verify modal opens\n"
                "   d. Fill: name='Logistics Domain', owner_team='ops-team', owner_email='ops@test.gov'\n"
                "   e. Submit — verify card appears in grid without page reload\n"
                "   f. Click card — verify maturity breakdown expands (product_count, contract_count, policy_count)\n"
                "   g. Verify maturity badge shows 'Defined' (new domain with no products/contracts)\n"
                "All must pass before Epic 3 begins."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dm-domain-03",
        },

        # ══════════════════════════════════════════════════════════════════════
        # Epic 3: Data Product Registry — /data/products
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-prod-01",
            "title": "Data Mesh: Create tools/data_canvas/data_mesh/product_registry.py",
            "description": (
                "Pure-function product registry module. No Flask, no LLM.\n"
                "Uses get_connection() from tools/data_canvas/db/init_db.py.\n\n"
                "Functions:\n"
                "  list_products(domain_id=None, status=None) -> list[dict]  — filter by domain/status\n"
                "  get_product(product_id: str) -> dict | None\n"
                "  create_product(data: dict) -> dict  — generate uuid, validate status in DM_PRODUCT_STATUS\n"
                "  update_product(product_id: str, data: dict) -> dict | None\n"
                "  delete_product(product_id: str) -> bool\n"
                "  get_product_slas(product_id: str) -> list[dict]\n"
                "  add_product_sla(product_id: str, sla_type: str, target: float, unit: str) -> dict\n"
                "  subscribe_to_product(product_id: str, subscriber: dict) -> dict\n"
                "    — insert into dm_product_subscriptions, approved=0\n"
                "  approve_subscription(sub_id: str) -> bool  — set approved=1\n"
                "  compute_discoverability_score(product_id: str) -> dict:\n"
                "    Scores 0-100 across 5 dimensions (20pts each):\n"
                "    - has_description (product.description not null/empty)\n"
                "    - has_slas (at least 1 SLA in dm_product_slas)\n"
                "    - has_contract (at least 1 active contract in dm_data_contracts)\n"
                "    - has_lineage (design_id linkage via dd_lineage entries)\n"
                "    - has_quality (dd_quality_runs with passed=1 in last 7 days)\n"
                "    Returns {score, dimensions, label: 'Undiscoverable'/'Emerging'/'Discoverable'/'Trusted'}"
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-domain-vv",
        },
        {
            "id": "dm-prod-02",
            "title": "Data Mesh: Add /products route + 5 API endpoints to blueprint.py",
            "description": (
                "Add inside create_data_canvas_blueprint() before return bp:\n\n"
                "  @bp.route('/products')\n"
                "  @dc_login_required\n"
                "  def dc_products(): render products.html with domains + products lists\n\n"
                "  @bp.route('/api/dm/products', methods=['GET', 'POST'])\n"
                "  GET: list_products(domain_id=request.args.get('domain_id'),\n"
                "                      status=request.args.get('status'))\n"
                "  POST: create_product(request.json)\n\n"
                "  @bp.route('/api/dm/products/<product_id>', methods=['GET', 'PUT', 'DELETE'])\n"
                "  CRUD via get/update/delete_product()\n\n"
                "  @bp.route('/api/dm/products/<product_id>/subscribe', methods=['POST'])\n"
                "  subscribe_to_product(product_id, request.json)\n\n"
                "  @bp.route('/api/dm/products/<product_id>/score')\n"
                "  compute_discoverability_score(product_id)\n\n"
                "Import product_registry functions at top of blueprint.py.\n"
                "Import DM_PRODUCT_STATUS, DM_OUTPUT_PORT_TYPES, DM_SLA_TIERS from constants."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-prod-01",
        },
        {
            "id": "dm-prod-03",
            "title": "Data Mesh: Create products.html + icdev/ mirror + nav update",
            "description": (
                "1. Create tools/dashboard/templates/data_canvas/products.html\n"
                "   Extends base.html. Dark theme consistent with domains.html.\n"
                "   Sections:\n"
                "   - Filter bar: domain selector + status pills (All/Draft/Published/Deprecated)\n"
                "   - Product catalog cards: name, domain badge, output_port_type icon,\n"
                "     SLA tier chip (Bronze/Silver/Gold/Platinum with color coding),\n"
                "     discoverability score ring (0-100, color: red<40, orange 40-70, green>70)\n"
                "   - Product detail drawer (slide-in on card click): full description,\n"
                "     SLA list, subscription list, 'Subscribe' button (opens subscribe modal),\n"
                "     discoverability score breakdown (5 dimensions)\n"
                "   - New Product modal: name, domain selector, description, output_port_type,\n"
                "     sla_tier, owner_team, classification\n"
                "   All data via fetch() — no page reload.\n\n"
                "2. Mirror to icdev/tools/dashboard/templates/data_canvas/products.html\n\n"
                "3. Add 'Products' link to Data Mesh nav section in index.html. Mirror index.html."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-prod-02",
        },
        {
            "id": "dm-prod-vv",
            "title": "Data Mesh Products V&V: coherence + companion sync + Playwright (/data/products)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright:\n"
                "   a. Navigate http://localhost:5050/data/products\n"
                "   b. Verify filter bar, product cards area renders\n"
                "   c. Click 'New Product' — fill name='Orders Dataset', select domain, output_port_type='table'\n"
                "   d. Submit — verify card appears with discoverability score ring\n"
                "   e. Click card — verify detail drawer slides in with Subscribe button\n"
                "   f. Click Subscribe — fill subscriber_team='analytics-team', purpose='reporting'\n"
                "   g. Submit subscription — verify subscription recorded (no page reload)\n"
                "   h. GET /api/dm/products/<id>/score — verify JSON includes all 5 dimensions\n"
                "All must pass before Epic 4."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dm-prod-03",
        },

        # ══════════════════════════════════════════════════════════════════════
        # Epic 4: Data Contracts — /data/contracts (ODCS/bitol-io)
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-contract-01",
            "title": "Data Mesh: Create tools/data_canvas/data_mesh/contract_engine.py",
            "description": (
                "Data Contract management engine (ODCS v1.1+ compatible). No Flask.\n"
                "Uses get_connection() from tools/data_canvas/db/init_db.py.\n\n"
                "ODCS required fields: dataContractSpecification, id, info.title,\n"
                "info.owner, models (at least 1).\n\n"
                "Functions:\n"
                "  list_contracts(domain_id=None, product_id=None) -> list[dict]\n"
                "  get_contract(contract_id: str) -> dict | None\n"
                "  create_contract(data: dict) -> dict\n"
                "  update_contract(contract_id: str, data: dict) -> dict | None\n"
                "  delete_contract(contract_id: str) -> bool\n\n"
                "  validate_yaml_structure(yaml_text: str) -> dict:\n"
                "    {valid: bool, fields_present: list, missing_required: list, warnings: list}\n"
                "    Pure Python yaml.safe_load() — no external deps beyond PyYAML.\n\n"
                "  lint_contract(yaml_text: str) -> dict:\n"
                "    {passed: bool, errors: list, warnings: list, score: float 0-100}\n"
                "    Checks: spec version present, id is URN, info.owner is set, models non-empty,\n"
                "    quality section present (warning if absent), servers section present (warning if absent).\n\n"
                "  test_contract(contract_id: str, conn_params: dict) -> dict:\n"
                "    Primary: try subprocess 'datacontract test --file <tmpfile> --server <conn>'\n"
                "    if datacontract-cli is importable (importlib.util.find_spec('datacontract')).\n"
                "    Fallback: run quality_engine.run_all_rules() for rules defined in contract.quality\n"
                "    mapped to existing dd_quality_rules for the linked product's design.\n"
                "    Returns {passed, error_count, warnings, result_json, method: 'cli'|'internal'}.\n"
                "    Store result in dm_contract_test_runs. Never raises."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-prod-vv",
        },
        {
            "id": "dm-contract-02",
            "title": "Data Mesh: Add /contracts route + 5 API endpoints to blueprint.py",
            "description": (
                "Add inside create_data_canvas_blueprint() before return bp:\n\n"
                "  @bp.route('/contracts')\n"
                "  @dc_login_required\n"
                "  def dc_contracts(): render contracts.html with domains + products + contracts\n\n"
                "  @bp.route('/api/dm/contracts', methods=['GET', 'POST'])\n"
                "  GET: list_contracts(domain_id=request.args.get('domain_id'))\n"
                "  POST: create_contract(request.json)\n\n"
                "  @bp.route('/api/dm/contracts/<contract_id>', methods=['GET', 'PUT', 'DELETE'])\n"
                "  CRUD + _audit() on each write.\n\n"
                "  @bp.route('/api/dm/contracts/<contract_id>/lint', methods=['POST'])\n"
                "  lint_contract(request.json.get('yaml_text', ''))\n\n"
                "  @bp.route('/api/dm/contracts/<contract_id>/test', methods=['POST'])\n"
                "  test_contract(contract_id, request.json.get('conn_params', {}))\n\n"
                "Import contract_engine functions at top of blueprint.py."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-contract-01",
        },
        {
            "id": "dm-contract-03",
            "title": "Data Mesh: Create contracts.html + icdev/ mirror + nav link",
            "description": (
                "1. Create tools/dashboard/templates/data_canvas/contracts.html\n"
                "   Extends base.html. Dark theme consistent with domains/products pages.\n"
                "   Sections:\n"
                "   - Contract list sidebar: contracts grouped by domain, click to load\n"
                "   - YAML editor panel: large textarea (monospace, 20 rows) pre-populated\n"
                "     with ODCS starter template on 'New Contract'\n"
                "     Starter template includes spec version, placeholder id, info.title/owner, empty models\n"
                "   - Lint panel (right of editor): 'Lint' button -> POST /api/dm/contracts/<id>/lint\n"
                "     Shows errors (red), warnings (yellow), score badge (0-100)\n"
                "   - Test panel: 'Run Tests' button -> POST /api/dm/contracts/<id>/test\n"
                "     Shows passed/failed, error_count, method used (CLI vs internal), result detail\n"
                "   - Test run history: last 10 runs with timestamp + pass/fail badge\n"
                "   - New Contract modal: name, domain selector, product selector, classification\n"
                "   All via fetch(). YAML saved on blur (auto-save via PUT).\n\n"
                "2. Mirror to icdev/tools/dashboard/templates/data_canvas/contracts.html\n\n"
                "3. Add 'Contracts' link to Data Mesh nav section in index.html. Mirror."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-contract-02",
        },
        {
            "id": "dm-contract-vv",
            "title": "Data Mesh Contracts V&V: coherence + companion sync + Playwright (/data/contracts)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright:\n"
                "   a. Navigate http://localhost:5050/data/contracts\n"
                "   b. Verify contract list sidebar + YAML editor panel render\n"
                "   c. Click 'New Contract' — fill name, domain, verify ODCS template appears in editor\n"
                "   d. Click 'Lint' — verify lint panel shows errors/warnings/score\n"
                "     (template should score ~60/100 with warnings for missing quality/servers)\n"
                "   e. Fix missing fields in YAML, re-lint — verify score improves to >80\n"
                "   f. Click 'Run Tests' — verify test result panel shows method (cli or internal)\n"
                "   g. Verify test run appears in history list\n"
                "All must pass before Epic 5."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dm-contract-03",
        },

        # ══════════════════════════════════════════════════════════════════════
        # Epic 5: Federated Governance — /data/governance (OPA + fallback)
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-gov-01",
            "title": "Data Mesh: Create tools/data_canvas/data_mesh/governance_engine.py",
            "description": (
                "Federated governance engine: OPA REST client with pure-Python fallback.\n"
                "Uses get_connection() from tools/data_canvas/db/init_db.py.\n"
                "OPA_URL from os.environ.get('ICDEV_OPA_URL', '') — empty = fallback mode.\n\n"
                "Functions:\n"
                "  list_policies(domain_id=None) -> list[dict]  — from dm_opa_policies\n"
                "  get_policy(policy_id: str) -> dict | None\n"
                "  create_policy(data: dict) -> dict  — store in dm_opa_policies\n"
                "  update_policy(policy_id: str, data: dict) -> dict | None\n"
                "  delete_policy(policy_id: str) -> bool\n\n"
                "  check_access(user_attrs: dict, resource: dict) -> dict:\n"
                "    {allowed: bool, reason: str, method: 'opa'|'local', policy_id: str|None}\n"
                "    If OPA_URL set: POST to {OPA_URL}/v1/data/datamesh/allow, timeout=2s\n"
                "    Fallback (OPA unavailable or OPA_URL empty): evaluate_locally(user_attrs, resource)\n"
                "    Log result to dm_policy_audit_log.\n\n"
                "  evaluate_locally(user_attrs: dict, resource: dict) -> dict:\n"
                "    Built-in CUI policy: allow if user_attrs.clearance in ['CUI','SECRET','TS']\n"
                "    and resource.classification == 'CUI // SP-CTI'.\n"
                "    Returns {allowed, reason, method: 'local'}.\n\n"
                "  compute_governance_score(domain_id=None) -> dict:\n"
                "    {score: float 0-100, domains_with_policy: int, total_domains: int,\n"
                "     coverage_pct: float, label: 'At Risk'|'Partial'|'Governed'}\n"
                "    At Risk < 40%, Partial 40-75%, Governed >= 75%.\n\n"
                "  get_policy_audit_log(domain_id=None, limit=50) -> list[dict]"
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-contract-vv",
        },
        {
            "id": "dm-gov-02",
            "title": "Data Mesh: Add /governance route + 5 API endpoints to blueprint.py",
            "description": (
                "Add inside create_data_canvas_blueprint() before return bp:\n\n"
                "  @bp.route('/governance')\n"
                "  @dc_login_required\n"
                "  def dc_governance(): render governance.html with domains, governance score\n\n"
                "  @bp.route('/api/dm/policies', methods=['GET', 'POST'])\n"
                "  GET: list_policies(domain_id=request.args.get('domain_id'))\n"
                "  POST: create_policy(request.json)\n\n"
                "  @bp.route('/api/dm/policies/<policy_id>', methods=['GET', 'PUT', 'DELETE'])\n"
                "  CRUD for dm_opa_policies.\n\n"
                "  @bp.route('/api/dm/governance/check', methods=['POST'])\n"
                "  check_access(user_attrs=request.json.get('user'),\n"
                "               resource=request.json.get('resource'))\n\n"
                "  @bp.route('/api/dm/governance/score')\n"
                "  compute_governance_score(domain_id=request.args.get('domain_id'))\n\n"
                "Import governance_engine functions at top of blueprint.py."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-gov-01",
        },
        {
            "id": "dm-gov-03",
            "title": "Data Mesh: Create governance.html + icdev/ mirror + nav link",
            "description": (
                "1. Create tools/dashboard/templates/data_canvas/governance.html\n"
                "   Extends base.html. Dark theme consistent with other DM pages.\n"
                "   Sections:\n"
                "   - Governance Score Card: big ring gauge (0-100), label At Risk/Partial/Governed,\n"
                "     domain coverage fraction (e.g. '3 of 5 domains have active policies')\n"
                "   - Policy Manager: domain selector + policy list table\n"
                "     (name, domain, enabled toggle, Edit/Delete actions)\n"
                "   - Policy Editor drawer: Rego text area (monospace, 15 rows),\n"
                "     policy_path field, domain selector, enabled checkbox, Save button\n"
                "     Starter Rego template shown on 'New Policy':\n"
                "       package datamesh\\ndefault allow = false\\nallow {\\n  input.user.clearance != \\\"\\\"\\n}\n"
                "   - Access Check Tester: two JSON textareas (user_attrs + resource),\n"
                "     'Check Access' button -> POST /api/dm/governance/check,\n"
                "     shows allowed badge (green/red) + reason + method (OPA/local)\n"
                "   - Audit Log table: last 20 entries from dm_policy_audit_log\n"
                "   All via fetch(). Policy saves auto on blur.\n\n"
                "2. Mirror to icdev/tools/dashboard/templates/data_canvas/governance.html\n\n"
                "3. Add 'Governance' link to Data Mesh nav section in index.html. Mirror."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dm-gov-02",
        },
        {
            "id": "dm-gov-vv",
            "title": "Data Mesh Governance V&V: coherence + companion sync + Playwright (/data/governance)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright:\n"
                "   a. Navigate http://localhost:5050/data/governance\n"
                "   b. Verify governance score ring, policy list, access tester render\n"
                "   c. Click 'New Policy' — verify Rego starter template appears in editor\n"
                "   d. Fill policy name, select domain, save — verify appears in policy list\n"
                "   e. In Access Check Tester: enter user_attrs={\"clearance\":\"CUI\"},\n"
                "      resource={\"classification\":\"CUI // SP-CTI\"}\n"
                "   f. Click 'Check Access' — verify allowed=true, method='local' in response\n"
                "   g. Verify audit log entry appears with the decision\n"
                "   h. GET /api/dm/governance/score — verify JSON includes score, coverage_pct, label\n"
                "All must pass before Epic 6."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dm-gov-03",
        },

        # ══════════════════════════════════════════════════════════════════════
        # Epic 6: CSP Bridges — /data/csp (AWS DataZone / Azure Purview / GCP Dataplex)
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-csp-01",
            "title": "Data Mesh: Create tools/data_canvas/data_mesh/csp/ package with 3 adapters",
            "description": (
                "Create tools/data_canvas/data_mesh/csp/__init__.py + 3 adapter modules.\n"
                "All adapters: graceful degradation if CSP SDK not installed; dry-run mode;\n"
                "return {provider, status, synced_count, errors: list, dry_run: bool}.\n\n"
                "aws_datazone.py — AWS DataZone adapter:\n"
                "  requires: boto3. Check: importlib.util.find_spec('boto3').\n"
                "  sync_domains_to_datazone(domain_list, dry_run=True) -> result dict\n"
                "  sync_products_to_datazone(product_list, dry_run=True) -> result dict\n"
                "  Reads: AWS_DATAZONE_DOMAIN_ID, AWS_REGION from os.environ.\n"
                "  Maps dm_domains -> DataZone Domain Projects; dm_data_products -> DataZone Assets.\n"
                "  Uses boto3 client 'datazone' create_project() / create_asset().\n\n"
                "azure_purview.py — Azure Purview (Microsoft Purview) adapter:\n"
                "  requires: azure-purview-catalog (pip). Check via importlib.\n"
                "  sync_domains_to_purview(domain_list, dry_run=True) -> result dict\n"
                "  sync_products_to_purview(product_list, dry_run=True) -> result dict\n"
                "  Reads: AZURE_PURVIEW_ENDPOINT, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID.\n"
                "  Maps dm_domains -> Purview Collections; dm_data_products -> Purview DataSet entities.\n\n"
                "gcp_dataplex.py — GCP Dataplex adapter:\n"
                "  requires: google-cloud-dataplex (pip). Check via importlib.\n"
                "  sync_domains_to_dataplex(domain_list, dry_run=True) -> result dict\n"
                "  sync_products_to_dataplex(product_list, dry_run=True) -> result dict\n"
                "  Reads: GCP_PROJECT_ID, GCP_LOCATION from os.environ.\n"
                "  Maps dm_domains -> Dataplex Lakes; dm_data_products -> Dataplex Zones.\n\n"
                "csp/__init__.py:\n"
                "  get_csp_status() -> dict {aws: {available, configured}, azure: {...}, gcp: {...}}\n"
                "  run_sync(provider: str, domain_ids: list, dry_run: bool) -> result dict\n"
                "  Log result to dm_csp_sync_log."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "dm-gov-vv",
        },
        {
            "id": "dm-csp-02",
            "title": "Data Mesh: Add /csp route + 3 API endpoints + csp.html + icdev/ mirror + nav",
            "description": (
                "Add inside create_data_canvas_blueprint() before return bp:\n\n"
                "  @bp.route('/csp')\n"
                "  @dc_login_required\n"
                "  def dc_csp(): render csp.html with csp_status from get_csp_status()\n\n"
                "  @bp.route('/api/dm/csp/status')\n"
                "  @dc_login_required\n"
                "  def dc_api_csp_status(): return get_csp_status()\n\n"
                "  @bp.route('/api/dm/csp/sync', methods=['POST'])\n"
                "  @dc_login_required\n"
                "  def dc_api_csp_sync():\n"
                "    provider = request.json.get('provider')\n"
                "    domain_ids = request.json.get('domain_ids', [])\n"
                "    dry_run = request.json.get('dry_run', True)\n"
                "    return run_sync(provider, domain_ids, dry_run)\n\n"
                "  @bp.route('/api/dm/csp/history')\n"
                "  @dc_login_required  \n"
                "  def dc_api_csp_history():\n"
                "    conn = get_connection()\n"
                "    rows = conn.execute('SELECT * FROM dm_csp_sync_log ORDER BY created_at DESC LIMIT 50').fetchall()\n"
                "    return jsonify([dict(r) for r in rows])\n\n"
                "Import csp functions at top of blueprint.py.\n\n"
                "Create csp.html: 3 provider cards (AWS DataZone, Azure Purview, GCP Dataplex).\n"
                "Each card: SDK availability badge (Available/Not Installed), credentials configured badge,\n"
                "domain selector multi-select, Dry Run toggle, 'Sync' button.\n"
                "Sync result panel: synced_count, error list, method badge.\n"
                "Sync history table: last 20 from dm_csp_sync_log.\n"
                "Mirror csp.html to icdev/. Add 'CSP' link to Data Mesh nav in index.html. Mirror."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "dm-csp-01",
        },
        {
            "id": "dm-csp-vv",
            "title": "Data Mesh CSP V&V: coherence + companion sync + Playwright (/data/csp)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright:\n"
                "   a. Navigate http://localhost:5050/data/csp\n"
                "   b. Verify 3 provider cards render with SDK availability badges\n"
                "   c. All 3 should show 'Not Installed' (SDK not in requirements by default) — acceptable\n"
                "   d. Select AWS DataZone card, enable Dry Run, click 'Sync'\n"
                "   e. Verify sync result panel shows dry_run=true, status, provider='aws_datazone'\n"
                "   f. Verify sync history table shows the attempt with correct provider/status\n"
                "   g. GET /api/dm/csp/status — verify JSON has aws, azure, gcp keys with available/configured\n"
                "All must pass before Epic 7."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "dm-csp-02",
        },

        # ══════════════════════════════════════════════════════════════════════
        # Epic 7: Data Mesh Hub — /data/mesh (4-pillar landing page)
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-portal-01",
            "title": "Data Mesh: Add /mesh route + /api/dm/summary to blueprint.py",
            "description": (
                "Add inside create_data_canvas_blueprint() before return bp:\n\n"
                "  @bp.route('/mesh')\n"
                "  @dc_login_required\n"
                "  def dc_mesh(): render mesh.html passing summary from _build_mesh_summary()\n\n"
                "  @bp.route('/api/dm/summary')\n"
                "  @dc_login_required\n"
                "  def dc_api_dm_summary(): return jsonify(_build_mesh_summary())\n\n"
                "Add _build_mesh_summary() as a private function in blueprint.py:\n"
                "  Queries all 4 pillars in a single function:\n"
                "  - domain_count: SELECT COUNT(*) FROM dm_domains\n"
                "  - product_count: SELECT COUNT(*) FROM dm_data_products WHERE status='published'\n"
                "  - contract_count: SELECT COUNT(*) FROM dm_data_contracts WHERE status='active'\n"
                "  - governance_score: compute_governance_score() from governance_engine\n"
                "  - recent_products: last 5 published products\n"
                "  - recent_contracts: last 5 active contracts\n"
                "  - pillar_scores: {\n"
                "      domain_ownership: min(domain_count*20, 100),  # up to 5 domains = 100%\n"
                "      data_products: min(product_count*10, 100),    # up to 10 products = 100%\n"
                "      contracts: min(contract_count*20, 100),        # up to 5 contracts = 100%\n"
                "      governance: governance_score.score\n"
                "    }\n"
                "  Returns {domain_count, product_count, contract_count, governance_score,\n"
                "    recent_products, recent_contracts, pillar_scores}"
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "dm-csp-vv",
        },
        {
            "id": "dm-portal-02",
            "title": "Data Mesh: Create mesh.html + icdev/ mirror + 'Data Mesh' link in base nav",
            "description": (
                "1. Create tools/dashboard/templates/data_canvas/mesh.html\n"
                "   Extends base.html. Dark theme (#16213e/#0f3460).\n"
                "   Layout: full-width hero + 4-pillar grid + activity feed.\n\n"
                "   Hero: 'Data Mesh Control Plane' title + overall health score\n"
                "   (average of 4 pillar_scores) as large ring gauge with color coding.\n\n"
                "   4-Pillar Score Cards (2x2 grid):\n"
                "   - Domain Ownership: score ring + domain_count domains + link to /data/domains\n"
                "   - Data Products: score ring + product_count published + link to /data/products\n"
                "   - Data Contracts: score ring + contract_count active + link to /data/contracts\n"
                "   - Federated Governance: score ring + governance label + link to /data/governance\n\n"
                "   Quick-Action Strip: [+ New Domain] [+ New Product] [+ New Contract] [CSP Sync]\n"
                "   each button deep-links to the relevant page.\n\n"
                "   Activity Feed (right panel): recent_products + recent_contracts interleaved,\n"
                "   sorted by created_at desc, with domain badge and status chip.\n\n"
                "   Data is fetched on page load from /api/dm/summary via JS fetch().\n"
                "   Auto-refresh every 60s.\n\n"
                "2. Mirror to icdev/tools/dashboard/templates/data_canvas/mesh.html\n\n"
                "3. In tools/dashboard/templates/base.html add 'Data Mesh' link in the\n"
                "   sidebar nav under the Data Design Canvas section, pointing to /data/mesh.\n"
                "   Mirror base.html to icdev/."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "dm-portal-01",
        },
        {
            "id": "dm-portal-vv",
            "title": "Data Mesh Hub V&V: coherence + companion sync + Playwright (/data/mesh)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright:\n"
                "   a. Navigate http://localhost:5050/data/mesh\n"
                "   b. Verify 4 pillar score cards render with rings + counts + links\n"
                "   c. Verify quick-action strip shows 4 buttons\n"
                "   d. Click '+ New Domain' — verify redirect to /data/domains\n"
                "   e. Navigate back to /data/mesh\n"
                "   f. GET /api/dm/summary — verify JSON has domain_count, product_count,\n"
                "      contract_count, pillar_scores (all 4 keys), recent_products, governance_score\n"
                "   g. Verify 'Data Mesh' link appears in sidebar nav (base.html)\n"
                "   h. Navigate to /data/ (index) — verify Data Mesh nav section shows\n"
                "      Domains | Products | Contracts | Governance | CSP | Mesh links\n"
                "All must pass before Epic 8."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "dm-portal-02",
        },

        # ══════════════════════════════════════════════════════════════════════
        # Epic 8: Wiring — OpenLineage emitter + requirements + manifest + V&V
        # ══════════════════════════════════════════════════════════════════════
        {
            "id": "dm-wire-01",
            "title": "Data Mesh: Create lineage_emitter.py + requirements.txt optional deps + data_mesh_config.yaml",
            "description": (
                "1. Create tools/data_canvas/data_mesh/lineage_emitter.py\n"
                "   OpenLineage standard event emission wrapper. No Flask.\n"
                "   _HAS_OL = bool(importlib.util.find_spec('openlineage'))\n\n"
                "   emit_lineage_event(run_id, job_name, inputs, outputs, state='COMPLETE') -> dict:\n"
                "     If _HAS_OL: use openlineage.client.OpenLineageClient to emit to\n"
                "     ICDEV_MARQUEZ_URL (default http://localhost:5000) as OpenLineage RunEvent.\n"
                "     Fallback: log to dd_lineage table (existing DDC lineage table) as internal event.\n"
                "     Returns {emitted: bool, method: 'openlineage'|'internal', run_id}.\n\n"
                "   emit_data_product_lineage(product_id, design_id) -> dict:\n"
                "     Reads dd_lineage edges for design_id, emits as OpenLineage DatasetFacets.\n"
                "     Ties the data product to its lineage provenance chain.\n\n"
                "2. In requirements.txt add optional dependency comments:\n"
                "   # Data Mesh optional deps (install as needed)\n"
                "   # datacontract-cli>=0.10.0  # ODCS contract linting/testing\n"
                "   # openlineage-python>=1.16.0  # OpenLineage standard emission\n"
                "   # pyiceberg>=0.7.0  # Apache Iceberg table format (requires pyarrow)\n"
                "   # boto3>=1.34.0  # AWS DataZone CSP bridge\n"
                "   # azure-purview-catalog>=1.0.0  # Azure Purview CSP bridge\n"
                "   # google-cloud-dataplex>=2.0.0  # GCP Dataplex CSP bridge\n\n"
                "3. Create args/data_mesh_config.yaml:\n"
                "   opa_url: ''  # Set to http://localhost:8181 when OPA running\n"
                "   marquez_url: 'http://localhost:5000'  # OpenLineage/Marquez server\n"
                "   default_classification: 'CUI // SP-CTI'\n"
                "   governance_score_gate: 0.6\n"
                "   csp_dry_run_default: true\n"
                "   contract_test_mode: 'internal'  # 'cli' requires datacontract-cli installed"
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "dm-portal-vv",
        },
        {
            "id": "dm-wire-02",
            "title": "Data Mesh: Create tools/manifest/data-mesh.md shard + update manifest.md index",
            "description": (
                "1. Create tools/manifest/data-mesh.md documenting all new Data Mesh modules:\n"
                "   ## Data Mesh (dm-*)\n"
                "   **Package:** tools/data_canvas/data_mesh/\n"
                "   **Routes:** /data/domains, /data/products, /data/contracts, /data/governance, /data/csp, /data/mesh\n\n"
                "   | Module | Purpose | CLI |\n"
                "   |--------|---------|-----|\n"
                "   | domain_manager.py | Domain CRUD + maturity scoring | No |\n"
                "   | product_registry.py | Product registry + SLA + subscriptions + discoverability | No |\n"
                "   | contract_engine.py | ODCS contract lint + test (datacontract-cli or internal) | No |\n"
                "   | governance_engine.py | OPA client + local policy eval + audit log | No |\n"
                "   | lineage_emitter.py | OpenLineage event emission (openlineage-python or internal) | No |\n"
                "   | csp/__init__.py | CSP status + sync router | No |\n"
                "   | csp/aws_datazone.py | AWS DataZone sync (boto3 optional) | No |\n"
                "   | csp/azure_purview.py | Azure Purview sync (azure-purview-catalog optional) | No |\n"
                "   | csp/gcp_dataplex.py | GCP Dataplex sync (google-cloud-dataplex optional) | No |\n\n"
                "   **DB Tables:** dm_domains, dm_data_products, dm_product_slas,\n"
                "   dm_product_subscriptions, dm_data_contracts, dm_contract_test_runs,\n"
                "   dm_opa_policies, dm_policy_audit_log, dm_csp_sync_log\n\n"
                "   **Config:** args/data_mesh_config.yaml\n"
                "   **Open Source Tools:** OpenMetadata (catalog), OpenLineage+Marquez (lineage),\n"
                "   datacontract-cli/ODCS (contracts), OPA (governance), pyiceberg (storage format),\n"
                "   Redpanda (streaming), Dagster (orchestration)\n"
                "   **CSP Native:** AWS DataZone/Lake Formation, Azure Purview, GCP Dataplex\n\n"
                "2. In tools/manifest.md add line in Data Design Canvas section:\n"
                "   '[data-mesh.md](manifest/data-mesh.md) — Data Mesh: Domains, Products, Contracts, Governance, CSP (9 tables, 6 modules, 6 pages)'"
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "dm-wire-01",
        },
        {
            "id": "dm-wire-03",
            "title": "Data Mesh: Final companion sync + coherence gate + feature doc",
            "description": (
                "1. python tools/dx/companion.py --sync --write --json\n"
                "   Must complete without errors. Verify all 6 DM pages appear in sync output.\n\n"
                "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "   Must pass all gates including:\n"
                "   - sandbox-coverage gate (data_mesh modules must have entries in docs/security/sandbox-coverage.md)\n"
                "   - karpathy_principles sync\n"
                "   - MCP tool registry check\n"
                "   If sandbox-coverage gate fails: add entry to docs/security/sandbox-coverage.md:\n"
                "   'tools/data_canvas/data_mesh/ — trusted-first-party (internal ICDEV data only)'\n\n"
                "3. Create docs/features/phase-dm-data-mesh.md documenting:\n"
                "   - All 6 new pages (/data/mesh, /domains, /products, /contracts, /governance, /csp)\n"
                "   - 4 Data Mesh pillars and how each is implemented\n"
                "   - Open source tools integrated (OPA, ODCS/datacontract-cli, OpenLineage, pyiceberg)\n"
                "   - CSP bridges (AWS DataZone, Azure Purview, GCP Dataplex) and activation steps\n"
                "   - 9 new DB tables and their purpose\n"
                "   - Configuration via args/data_mesh_config.yaml"
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "dm-wire-02",
        },
        {
            "id": "dm-wire-vv",
            "title": "Data Mesh FINAL E2E V&V: Full mesh walkthrough Playwright + coherence",
            "description": (
                "Final end-to-end validation of the complete Data Mesh integration.\n\n"
                "1. python tools/workflow/coherence_checker.py --all --fix --gate  (must be green)\n"
                "2. python tools/dx/companion.py --sync --write --json  (must be green)\n\n"
                "3. Playwright full walkthrough:\n"
                "   a. /data/ (index) — verify all 6 DM links visible in nav (Mesh/Domains/Products/Contracts/Governance/CSP)\n"
                "   b. /data/mesh — verify 4 pillar score cards, activity feed, quick-action strip\n"
                "   c. /data/domains — create domain 'Intelligence Domain', verify Defined maturity\n"
                "   d. /data/products — create product 'SIGINT Feed', link to domain, verify card + score ring\n"
                "   e. /data/contracts — create contract, paste ODCS YAML, lint (score >0), run test\n"
                "   f. /data/governance — create policy, run access check, verify audit log entry\n"
                "   g. /data/csp — verify 3 provider cards, run AWS dry-run sync, verify history\n"
                "   h. Return to /data/mesh — verify pillar scores have updated (domain=1, product=1)\n"
                "   i. GET /api/dm/summary — verify all fields present and non-zero\n\n"
                "4. python -c \"from tools.data_canvas.data_mesh.lineage_emitter import emit_lineage_event;\n"
                "   r=emit_lineage_event('test-run','test-job',[],[]);\n"
                "   assert r['emitted']; print('OpenLineage emitter OK:', r['method'])\"\n\n"
                "All checks must pass. This is the sign-off gate for the dm-* project."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dm-wire-03",
        },
    ]

    seeded = 0
    skipped = 0
    for t in tasks:
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id=?", (t["id"],)
        ).fetchone()
        if existing:
            print(f"  SKIP (exists): {t['id']}")
            skipped += 1
            continue
        conn.execute(
            """INSERT INTO kanban_tasks
               (id, title, description, task_type, priority, status,
                scheduled_at, created_at, updated_at, depends_on_task_id)
               VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?)""",
            (
                t["id"], t["title"], t["description"],
                t["task_type"], t["priority"],
                SCHED, NOW, NOW,
                t.get("depends_on_task_id"),
            ),
        )
        print(f"  SEEDED: {t['id']} — {t['title'][:65]}")
        seeded += 1

    conn.commit()
    conn.close()
    print(f"\nDone. {seeded} tasks seeded, {skipped} skipped for project dm-*")
    print("\nEpic dependency chain:")
    print("  found (dm-found-01→02→vv)")
    print("  → domain (dm-domain-01→02→03→vv)")
    print("  → prod (dm-prod-01→02→03→vv)")
    print("  → contract (dm-contract-01→02→03→vv)")
    print("  → gov (dm-gov-01→02→03→vv)")
    print("  → csp (dm-csp-01→02→vv)")
    print("  → portal (dm-portal-01→02→vv)")
    print("  → wire (dm-wire-01→02→03→vv)")


if __name__ == "__main__":
    seed()
