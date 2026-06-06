# CUI // SP-CTI — Seed DDC Data Science Kanban tasks
"""Seed kanban_tasks for the DDC Data Science integration project (dds-*)."""


import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402


NOW = datetime.now(timezone.utc).isoformat()
SCHED = NOW  # schedule immediately


def seed():
    tasks = [
        # ── Epic 1: Data Profiler (/data/explore) ──────────────────────────
        {
            "id": "dds-ex-01",
            "title": "DDC: Add dd_explore_profiles + dd_explore_sessions tables to init_db.py",
            "description": (
                "Add two new tables to SCHEMA string in tools/data_canvas/db/init_db.py:\n"
                "  dd_explore_profiles (id, design_id, db_conn_json, profile_json, classification, created_at)\n"
                "  dd_explore_sessions (id, design_id, user, db_conn_json, status, created_at)\n"
                "Use CREATE TABLE IF NOT EXISTS. Add covering indexes. No other changes."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": None,
        },
        {
            "id": "dds-ex-02",
            "title": "DDC: Create tools/data_canvas/data_profiler.py",
            "description": (
                "Pure-function profiler module. No Flask, no LLM.\n"
                "profile_database(conn_params, classification) -> {tables, row_counts, columns, profiles}\n"
                "profile_table(conn, table, classification) -> {name, row_count, columns[{name, null_pct, distinct_count, min, max, top_values, inferred_type}]}\n"
                "Supports db_type: sqlite, postgresql (via psycopg2 if available), duckdb (if available).\n"
                "Gracefully degrades to available driver. All returned dicts include classification field."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-ex-01",
        },
        {
            "id": "dds-ex-03",
            "title": "DDC: Add /explore page route + 3 API endpoints to blueprint.py",
            "description": (
                "Add inside create_data_canvas_blueprint() before return bp:\n"
                "  @bp.route('/explore') dc_explore() -> render explore.html (pass designs list)\n"
                "  @bp.route('/api/explore/profile', POST) dc_api_explore_profile() -> run data_profiler.profile_database(), store in dd_explore_profiles, return JSON\n"
                "  @bp.route('/api/explore/profiles', GET) dc_api_explore_list() -> list dd_explore_profiles ordered by created_at DESC\n"
                "  @bp.route('/api/explore/sessions', GET) dc_api_explore_sessions() -> list dd_explore_sessions\n"
                "All routes @dc_login_required. Audit log entry on profile run."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-ex-02",
        },
        {
            "id": "dds-ex-04",
            "title": "DDC: Create tools/dashboard/templates/data_canvas/explore.html",
            "description": (
                "Extends base.html. Dark theme (#16213e/#ff9800). Three panels:\n"
                "  1. Connection panel: db_type dropdown (sqlite/postgresql/duckdb), host/port/user/pass/db fields, design selector dropdown, Run Profile button\n"
                "  2. Table selector: list of tables returned from profile, click to expand\n"
                "  3. Column profiles: for each column — null% bar, distinct count, min/max, top-5 value chips\n"
                "Classification badge rendered for each column per its marking.\n"
                "Nav: include Explore/Query/Quality links in a secondary nav bar at top of page.\n"
                "Uses fetch() POST to /data/api/explore/profile. No page reload."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-ex-03",
        },
        {
            "id": "dds-ex-05",
            "title": "DDC: Mirror explore.html to icdev/tools/dashboard/templates/data_canvas/",
            "description": (
                "Copy tools/dashboard/templates/data_canvas/explore.html to\n"
                "icdev/tools/dashboard/templates/data_canvas/explore.html.\n"
                "Files must be byte-for-byte identical."
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "dds-ex-04",
        },
        {
            "id": "dds-ex-06",
            "title": "DDC: Update index.html + args config for explore tab",
            "description": (
                "1. In tools/dashboard/templates/data_canvas/index.html add 'Explore' link in the quick-nav bar alongside Assessments/Templates/Runbooks/SOPs.\n"
                "2. In args/data_canvas_config.yaml add explore section: enabled: true, max_rows_preview: 50000, top_values_limit: 10.\n"
                "Mirror index.html change to icdev/."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "dds-ex-05",
        },
        {
            "id": "dds-ex-vv",
            "title": "DDC Epic 1 V&V: CodeLens + Coherence + E2E Playwright (/data/explore)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright: navigate /data/explore, verify page loads, connection panel present, run a profile against data/data_canvas.db, verify column cards render.\n"
                "4. Verify classification badge appears on PII columns.\n"
                "All must pass before Epic 2 begins."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dds-ex-06",
        },
        # ── Epic 2: SQL Sandbox (/data/query) ──────────────────────────────
        {
            "id": "dds-qr-01",
            "title": "DDC: Add dd_query_history table to init_db.py",
            "description": (
                "Add to SCHEMA in tools/data_canvas/db/init_db.py:\n"
                "  dd_query_history (id TEXT PK, design_id TEXT, user TEXT, sql_text TEXT, db_conn_json TEXT,\n"
                "                    row_count INTEGER, exec_ms INTEGER, classification TEXT DEFAULT 'CUI // SP-CTI',\n"
                "                    created_at TEXT DEFAULT CURRENT_TIMESTAMP)\n"
                "Add index idx_dd_query_history_design on design_id."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-ex-vv",
        },
        {
            "id": "dds-qr-02",
            "title": "DDC: Create tools/data_canvas/query_sandbox.py",
            "description": (
                "Pure-function SQL sandbox module. No Flask, no LLM.\n"
                "execute_query(sql, conn_params, classification) -> {columns, rows, row_count, exec_ms, classification, error}\n"
                "validate_query(sql) -> {valid, error} — only SELECT allowed (strip/upper check + sqlparse if available)\n"
                "Supports sqlite, postgresql, duckdb. Hard limit: 1000 rows returned.\n"
                "Returns error dict if non-SELECT or exception, never raises."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-qr-01",
        },
        {
            "id": "dds-qr-03",
            "title": "DDC: Add /query page + 3 API routes to blueprint.py",
            "description": (
                "Add to blueprint.py before return bp:\n"
                "  @bp.route('/query') dc_query() -> render query.html\n"
                "  @bp.route('/api/query/execute', POST) dc_api_query_execute() -> validate + run query, store in dd_query_history, return JSON\n"
                "  @bp.route('/api/query/history', GET) dc_api_query_history() -> list last 50 from dd_query_history ordered by created_at DESC\n"
                "All @dc_login_required. Reject non-SELECT with 400."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-qr-02",
        },
        {
            "id": "dds-qr-04",
            "title": "DDC: Create tools/dashboard/templates/data_canvas/query.html",
            "description": (
                "Extends base.html. Same dark theme. Three areas:\n"
                "  1. Connection panel (same as explore.html — db_type, host/port/user/pass/db)\n"
                "  2. SQL editor textarea (monospace, min 8 rows, syntax hint overlay showing SELECT only)\n"
                "  3. Results grid: column headers + rows, pagination at 100/page, export CSV button\n"
                "  4. History panel: last 10 queries as clickable chips (click to reload SQL)\n"
                "Classification badge on results header. Error banner if non-SELECT or DB error.\n"
                "Secondary nav bar same as explore.html."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-qr-03",
        },
        {
            "id": "dds-qr-05",
            "title": "DDC: Mirror query.html to icdev/ + update index.html Query link",
            "description": (
                "1. Copy tools/dashboard/templates/data_canvas/query.html to icdev/ mirror path.\n"
                "2. Add 'Query' link to index.html quick-nav bar (alongside Explore).\n"
                "Mirror index.html update to icdev/."
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "dds-qr-04",
        },
        {
            "id": "dds-qr-vv",
            "title": "DDC Epic 2 V&V: CodeLens + Coherence + E2E Playwright (/data/query)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright: navigate /data/query, verify SQL editor and connection panel render.\n"
                "4. Submit 'SELECT 1 AS test', verify results grid shows row with test=1.\n"
                "5. Submit 'DROP TABLE users', verify 400 error banner appears.\n"
                "6. Verify history panel shows the SELECT 1 query."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dds-qr-05",
        },
        # ── Epic 3: Data Quality Rules (/data/quality) ─────────────────────
        {
            "id": "dds-ql-01",
            "title": "DDC: Add dd_quality_rules + dd_quality_runs tables to init_db.py",
            "description": (
                "Add to SCHEMA:\n"
                "  dd_quality_rules (id TEXT PK, design_id TEXT, name TEXT, table_name TEXT, column_name TEXT,\n"
                "                    check_type TEXT, threshold REAL, params_json TEXT DEFAULT '{}',\n"
                "                    classification TEXT DEFAULT 'CUI // SP-CTI', enabled INTEGER DEFAULT 1,\n"
                "                    created_at TEXT, updated_at TEXT)\n"
                "  dd_quality_runs (id TEXT PK, rule_id TEXT REFERENCES dd_quality_rules(id) ON DELETE CASCADE,\n"
                "                   db_conn_json TEXT, passed INTEGER, actual_value REAL, threshold REAL,\n"
                "                   detail TEXT DEFAULT '', classification TEXT DEFAULT 'CUI // SP-CTI',\n"
                "                   created_at TEXT)\n"
                "Add indexes on design_id and rule_id."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-qr-vv",
        },
        {
            "id": "dds-ql-02",
            "title": "DDC: Create tools/data_canvas/quality_engine.py",
            "description": (
                "Pure-function quality engine. No Flask, no LLM.\n"
                "CHECK_TYPES = ['completeness', 'uniqueness', 'range', 'pattern', 'freshness']\n"
                "run_rule(rule_dict, conn_params) -> {passed, actual_value, threshold, detail, classification}\n"
                "  completeness: pct_non_null = COUNT(*) WHERE col IS NOT NULL / COUNT(*) >= threshold\n"
                "  uniqueness:   pct_distinct = COUNT(DISTINCT col) / COUNT(*) >= threshold\n"
                "  range:        MIN(col) >= params.min_val AND MAX(col) <= params.max_val\n"
                "  pattern:      pct_match of regex (Python re) against sampled rows >= threshold (sqlite only)\n"
                "  freshness:    MAX(col) >= NOW - params.days days\n"
                "run_all_rules(design_id, conn_params, db) -> [{rule, result}] (db = DDC get_connection())\n"
                "quality_score(run_results) -> float 0-100 (pct rules passed)\n"
                "Never raises — returns error dict on DB failure."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-ql-01",
        },
        {
            "id": "dds-ql-03",
            "title": "DDC: Add /quality page + 5 API routes to blueprint.py",
            "description": (
                "Add to blueprint.py before return bp:\n"
                "  @bp.route('/quality') dc_quality() -> render quality.html (pass designs)\n"
                "  @bp.route('/api/quality/rules', GET) -> list rules (filter by design_id if param)\n"
                "  @bp.route('/api/quality/rules', POST) -> create rule, store in dd_quality_rules\n"
                "  @bp.route('/api/quality/rules/<rule_id>', DELETE) -> delete rule\n"
                "  @bp.route('/api/quality/run', POST) -> run all rules for a design, store in dd_quality_runs, return summary\n"
                "  @bp.route('/api/quality/runs', GET) -> last 100 runs ordered by created_at DESC\n"
                "All @dc_login_required."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-ql-02",
        },
        {
            "id": "dds-ql-04",
            "title": "DDC: Create tools/dashboard/templates/data_canvas/quality.html",
            "description": (
                "Extends base.html. Same dark theme. Four sections:\n"
                "  1. Quality score gauge (big number, 0-100, color green/orange/red based on score)\n"
                "  2. Rule builder form: design selector, table name, column name, check_type dropdown, threshold slider, Add Rule button\n"
                "  3. Rules table: name, table.column, check_type, threshold, enabled toggle, Delete button\n"
                "  4. Run history: last 20 runs with passed/failed badge, actual_value, threshold\n"
                "  Run All button triggers POST /api/quality/run, refreshes score gauge.\n"
                "  Secondary nav bar (Explore/Query/Quality) same as other pages."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "dds-ql-03",
        },
        {
            "id": "dds-ql-05",
            "title": "DDC: Mirror quality.html to icdev/ + update index.html + assessment bridge",
            "description": (
                "1. Copy quality.html to icdev/ mirror.\n"
                "2. Add 'Quality' link to index.html quick-nav bar.\n"
                "3. In data_engine.py assess_data_design(): add quality dimension — query latest dd_quality_runs for design, compute quality_score, add as finding DDC-QUA-001 if score < 70.\n"
                "Mirror index.html update to icdev/."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "dds-ql-04",
        },
        {
            "id": "dds-ql-vv",
            "title": "DDC Epic 3 V&V: CodeLens + Coherence + E2E Playwright (/data/quality)",
            "description": (
                "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
                "2. python tools/dx/companion.py --sync --write --json\n"
                "3. Playwright: navigate /data/quality, verify score gauge, rule builder, rules table render.\n"
                "4. Add a completeness rule via UI, verify it appears in rules table.\n"
                "5. Run all rules, verify score gauge updates.\n"
                "6. Navigate /data/assessments, verify DDC-QUA-001 appears in findings (if score < 70)."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "dds-ql-05",
        },
        # ── Epic 4: Infrastructure ──────────────────────────────────────────
        {
            "id": "dds-inf-01",
            "title": "DDC: Add data-science constants to constants.py + manifest shard",
            "description": (
                "1. In tools/data_canvas/constants.py add:\n"
                "   DS_CHECK_TYPES = ['completeness', 'uniqueness', 'range', 'pattern', 'freshness']\n"
                "   DS_DB_TYPES = ['sqlite', 'postgresql', 'duckdb']\n"
                "   DS_PROFILER_MAX_ROWS = 50000\n"
                "   DS_QUERY_MAX_ROWS = 1000\n"
                "2. Add entry to tools/manifest/design-canvases.md (or create new shard tools/manifest/ddc-data-science.md) covering data_profiler, query_sandbox, quality_engine."
            ),
            "task_type": "chore",
            "priority": "medium",
            "depends_on_task_id": "dds-ql-vv",
        },
        {
            "id": "dds-inf-02",
            "title": "DDC: Final companion sync + feature doc",
            "description": (
                "1. python tools/dx/companion.py --sync --write --json\n"
                "2. Create docs/features/phase-dds-ddc-data-science.md documenting all 3 features.\n"
                "3. python tools/workflow/coherence_checker.py --all --fix --gate (must be green)."
            ),
            "task_type": "chore",
            "priority": "medium",
            "depends_on_task_id": "dds-inf-01",
        },
    ]

    # These tasks are genuinely time-deferred: status 'scheduled' WITH a real
    # scheduled_at (SCHED) — schedule immediately. Stamp both on every task.
    for t in tasks:
        t["status"] = "scheduled"
        t["scheduled_at"] = SCHED

    report = create_tasks("dds", tasks, strict=False, register_project=False)
    print(report.summary())
    print("\nDone. 19 tasks seeded for project dds-*")


if __name__ == "__main__":
    seed()
