from __future__ import annotations
"""Seed DDX — DDC Data Science Extensions (5 epics, 26 tasks).
CUI // SP-CTI
Run: python tools/kanban/seed_ddx_extensions.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

TASKS = [
    # ── Epic an: LLM Anomaly Detection ───────────────────────────────────────
    {
        "id": "ddx-an-01",
        "title": "[ddx-an] anomaly_detector.py — pure function detect_anomalies()",
        "description": (
            "Create tools/data_canvas/anomaly_detector.py. "
            "Exports detect_anomalies(profile_dict, conn_params, classification) -> dict. "
            "Calls LLMRouter for code_generation function. "
            "Prompt: given column stats (null_pct, distinct_count, min/max, top_values, inferred_type), "
            "surface anomalies: high null rate (>40%), zero-cardinality, suspicious distributions, "
            "potential PII column names (email, ssn, phone, dob, zip), outlier min/max vs distribution. "
            "Returns {findings: [{column, severity, finding_type, description}], "
            "overall_risk, classification, analyzed_at}. "
            "No Flask, no DB writes — pure function only. "
            "Graceful fallback if LLM unavailable: rule-based heuristics (null_pct>40 => high, "
            "null_pct>20 => medium, distinct_count==0 => high)."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "ddx-an-02",
        "title": "[ddx-an] blueprint.py — POST /data/api/explore/analyze route",
        "description": (
            "Add route to tools/data_canvas/blueprint.py inside create_data_canvas_blueprint(). "
            "@bp.route('/data/api/explore/analyze', methods=['POST']) @dc_login_required. "
            "Body JSON: {profile_id, conn_params, classification}. "
            "Load profile JSON from dd_explore_profiles by profile_id. "
            "Call detect_anomalies(profile_dict, conn_params, classification). "
            "Store result in new column anomaly_json on dd_explore_profiles (ALTER TABLE or migration). "
            "Return {findings, overall_risk, profile_id, classification}. "
            "Return HTTP 400 if profile not found."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": "ddx-an-01",
    },
    {
        "id": "ddx-an-03",
        "title": "[ddx-an] DB migration — add anomaly_json to dd_explore_profiles",
        "description": (
            "Add migration to tools/data_canvas/db/init_db.py SCHEMA. "
            "ALTER TABLE dd_explore_profiles ADD COLUMN anomaly_json TEXT; — wrapped in "
            "try/except OperationalError (column already exists). "
            "Also add CREATE TABLE IF NOT EXISTS dd_anomaly_runs "
            "(id TEXT PRIMARY KEY, profile_id TEXT, findings_json TEXT, overall_risk TEXT, "
            "classification TEXT, created_at TEXT). "
            "Run tools/data_canvas/db/init_db.py --reinit to apply."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": "ddx-an-02",
    },
    {
        "id": "ddx-an-04",
        "title": "[ddx-an] explore.html — Analyze button + findings panel",
        "description": (
            "Update tools/dashboard/templates/data_canvas/explore.html. "
            "Add 'Analyze with AI' button (accent #ff9800) to each table card header. "
            "On click: POST /data/api/explore/analyze with {profile_id, conn_params, classification}. "
            "Show loading spinner during request. "
            "On response: render findings panel below the table card — "
            "severity chip (HIGH=red, MEDIUM=amber, LOW=blue) + column name + description for each finding. "
            "Show overall_risk badge (CRITICAL/HIGH/MEDIUM/LOW/CLEAN) with matching color. "
            "Dismiss tour modal before interactions (use existing dismissTour() pattern). "
            "Classification banner present on findings panel."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": "ddx-an-03",
    },
    {
        "id": "ddx-an-05",
        "title": "[ddx-an] icdev/ mirror + manifest entry for anomaly_detector",
        "description": (
            "Mirror explore.html to icdev/tools/dashboard/templates/data_canvas/explore.html "
            "using shutil.copy2(). "
            "Add anomaly_detector.py entry to tools/manifest/ddc-data-science.md: "
            "Path, Purpose, Key functions (detect_anomalies, _rule_based_fallback), "
            "LLM dependency (optional — graceful fallback), Classification handling."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ddx-an-04",
    },
    {
        "id": "ddx-an-06",
        "title": "[ddx-an] V&V — CodeLens + Coherence + E2E Playwright (anomaly)",
        "description": (
            "Phase 1 V&V gate. Run in order:\n"
            "1. python tools/analysis/code_lens.py --file tools/data_canvas/anomaly_detector.py --json\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "3. E2E Playwright: start app, navigate /data/explore, connect to :memory: SQLite, "
            "run profile on sqlite_master, click Analyze, assert findings panel appears "
            "with at least one finding card and classification badge. "
            "4. python tools/dx/companion.py --sync --write --json\n"
            "All must pass before marking complete."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "ddx-an-05",
    },

    # ── Epic pii: SQL PII/CUI Auto-Detection ─────────────────────────────────
    {
        "id": "ddx-pii-01",
        "title": "[ddx-pii] pii_scanner.py — scan_result() column + value checks",
        "description": (
            "Create tools/data_canvas/pii_scanner.py. "
            "Exports scan_result(columns: list[str], rows: list[list], classification: str) -> dict. "
            "Step 1 — column-name heuristics: match names against PII_COLUMN_PATTERNS dict "
            "(email, mail -> EMAIL; ssn, social -> SSN; phone, mobile, cell -> PHONE; "
            "dob, birth, birthday -> DOB; zip, postal -> ZIP; ip_addr, ip -> IP; "
            "passport, passport_no -> PASSPORT; credit, card_num -> CREDIT_CARD). "
            "Step 2 — value sampling (first 100 rows): run compiled regex patterns against "
            "non-null values in flagged columns. "
            "Returns {warnings: [{col, pii_type, confidence, hit_count, sample_count, severity}], "
            "has_warnings: bool, classification, scanned_at}. "
            "Severity: HIGH if regex confirms match, MEDIUM if column-name only. "
            "Pure function — no Flask, no DB, no LLM."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": "ddx-an-06",
    },
    {
        "id": "ddx-pii-02",
        "title": "[ddx-pii] query_sandbox.py — integrate pii_scanner into execute_query()",
        "description": (
            "Update tools/data_canvas/query_sandbox.py execute_query(). "
            "After fetching rows, call pii_scanner.scan_result(col_names, rows, classification). "
            "Add pii_warnings key to return dict: "
            "{..., pii_warnings: {warnings: [...], has_warnings: bool}}. "
            "Import guard: 'from tools.data_canvas.pii_scanner import scan_result as pii_scan'. "
            "Do NOT block query execution — warnings are advisory only. "
            "pii_warnings always present in response (empty warnings list if clean)."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": "ddx-pii-01",
    },
    {
        "id": "ddx-pii-03",
        "title": "[ddx-pii] query.html — PII warning banner above results grid",
        "description": (
            "Update tools/dashboard/templates/data_canvas/query.html. "
            "After receiving query results, check response.pii_warnings.has_warnings. "
            "If true: render amber banner ABOVE the results table: "
            "'⚠ CUI Sensitivity Detected — N column(s) contain potential PII/CUI data: [col list]'. "
            "Each warning chip shows pii_type + severity (HIGH=red, MEDIUM=amber). "
            "Banner is dismissable (×). "
            "If no warnings: no banner rendered (no empty placeholder). "
            "Classification badge on results table already present — no change needed there."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": "ddx-pii-02",
    },
    {
        "id": "ddx-pii-04",
        "title": "[ddx-pii] icdev/ mirror + manifest entry for pii_scanner",
        "description": (
            "Mirror query.html to icdev/tools/dashboard/templates/data_canvas/query.html. "
            "Add pii_scanner.py entry to tools/manifest/ddc-data-science.md: "
            "Path, Purpose, Key functions (scan_result), PII_COLUMN_PATTERNS list, "
            "Regex patterns used, Security note (advisory-only, does not block queries)."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ddx-pii-03",
    },
    {
        "id": "ddx-pii-05",
        "title": "[ddx-pii] V&V — CodeLens + Coherence + E2E Playwright (PII)",
        "description": (
            "Phase 2 V&V gate. Run in order:\n"
            "1. python tools/analysis/code_lens.py --file tools/data_canvas/pii_scanner.py --json\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "3. E2E Playwright: navigate /data/query, connect to dd_quality_rules table "
            "(column: column_name), run SELECT column_name, table_name FROM dd_quality_rules LIMIT 10, "
            "assert PII banner absent (column_name is not a PII column). "
            "Then run a synthetic query with email-named column alias "
            "(SELECT id as email FROM dd_quality_rules LIMIT 5), "
            "assert PII warning banner appears with 'EMAIL' pii_type chip. "
            "4. python tools/dx/companion.py --sync --write --json\n"
            "All must pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "ddx-pii-04",
    },

    # ── Epic cli: Data Profiler CLI ───────────────────────────────────────────
    {
        "id": "ddx-cli-01",
        "title": "[ddx-cli] data_profiler.py — add CLI __main__ block",
        "description": (
            "Append CLI entry point to tools/data_canvas/data_profiler.py. "
            "if __name__ == '__main__': use argparse. "
            "Commands: --profile (run profile_database), --list-tables (run list_tables). "
            "Flags: --db-type [sqlite|postgresql|duckdb] (default: sqlite), "
            "--path PATH (sqlite file path), --database DB, --host HOST, --port PORT, "
            "--user USER, --password PASSWORD, --tables T1 T2 (nargs=*), "
            "--classification CUI (default: 'CUI // SP-CTI'), "
            "--max-rows N (override DS_PROFILER_MAX_ROWS), "
            "--json (output JSON), --output FILE (write JSON to file). "
            "On --profile: call profile_database(conn_params, classification, tables). "
            "On --list-tables: call list_tables(conn_params). "
            "Print JSON if --json, else pretty-print table summary: "
            "table name | row_count | columns | null_avg%."
        ),
        "task_type": "feature",
        "priority": "medium",
        "depends_on_task_id": "ddx-pii-05",
    },
    {
        "id": "ddx-cli-02",
        "title": "[ddx-cli] tools/data_canvas/__main__.py — module invocation support",
        "description": (
            "Create tools/data_canvas/__main__.py to support "
            "'python -m tools.data_canvas' and 'python -m tools.data_canvas.data_profiler'. "
            "Content: from tools.data_canvas.data_profiler import main; main() "
            "where main() is extracted from the __main__ block. "
            "Refactor: move the argparse block in data_profiler.py into def main(): "
            "and call it from if __name__ == '__main__': main()."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ddx-cli-01",
    },
    {
        "id": "ddx-cli-03",
        "title": "[ddx-cli] manifest + commands.md update for profiler CLI",
        "description": (
            "Update tools/manifest/ddc-data-science.md: add CLI Usage section to data_profiler.py entry. "
            "Update docs/reference/commands.md: add under Data Design Canvas section:\n"
            "  python -m tools.data_canvas.data_profiler --list-tables --db-type sqlite --path data.db --json\n"
            "  python -m tools.data_canvas.data_profiler --profile --db-type sqlite --path data.db --json\n"
            "  python -m tools.data_canvas.data_profiler --profile --db-type postgresql --host localhost --database mydb --user user --json"
        ),
        "task_type": "chore",
        "priority": "low",
        "depends_on_task_id": "ddx-cli-02",
    },
    {
        "id": "ddx-cli-04",
        "title": "[ddx-cli] V&V — CodeLens + Coherence + CLI smoke test",
        "description": (
            "Phase 3 V&V gate. Run in order:\n"
            "1. python tools/analysis/code_lens.py --file tools/data_canvas/data_profiler.py --json\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "3. CLI smoke: python -m tools.data_canvas.data_profiler --list-tables "
            "--db-type sqlite --path data/data_canvas.db --json "
            "→ assert exit 0 and JSON contains 'tables' key. "
            "4. python -m tools.data_canvas.data_profiler --profile "
            "--db-type sqlite --path data/data_canvas.db --tables dd_quality_rules --json "
            "→ assert JSON contains 'tables' list with at least one entry and 'columns' key. "
            "5. python tools/dx/companion.py --sync --write --json\n"
            "All must pass."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "ddx-cli-03",
    },

    # ── Epic fr: Streaming Freshness Guardian ─────────────────────────────────
    {
        "id": "ddx-fr-01",
        "title": "[ddx-fr] dd_freshness_alerts migration in init_db.py",
        "description": (
            "Add to tools/data_canvas/db/init_db.py SCHEMA string: "
            "CREATE TABLE IF NOT EXISTS dd_freshness_alerts "
            "(id TEXT PRIMARY KEY, rule_id TEXT NOT NULL REFERENCES dd_quality_rules(id), "
            "design_id TEXT, db_conn_json TEXT, last_checked TEXT, passed INTEGER, "
            "actual_max_value TEXT, cutoff_value TEXT, detail TEXT, "
            "classification TEXT DEFAULT 'CUI // SP-CTI', created_at TEXT). "
            "Also add ALTER TABLE dd_quality_runs ADD COLUMN reflex_run TEXT "
            "(idempotent — wrapped in try/except). "
            "Run tools/data_canvas/db/init_db.py to verify."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ddx-cli-04",
    },
    {
        "id": "ddx-fr-02",
        "title": "[ddx-fr] freshness_guardian.py — genesis reflex (hourly freshness checks)",
        "description": (
            "Create tools/genesis/reflexes/freshness_guardian.py. "
            "Class FreshnessGuardianReflex with run(context, db_conn) method. "
            "Steps:\n"
            "1. Query dd_quality_rules WHERE check_type='freshness' AND enabled=1 "
            "(join across all designs — use get_connection() for data_canvas.db). "
            "2. For each rule: decode db_conn_json, call quality_engine._run_freshness(). "
            "3. Write result to dd_freshness_alerts (INSERT OR REPLACE). "
            "4. Write to dd_quality_runs with reflex_run=reflex_id. "
            "5. If any fail: write genesis_audit entry with event='freshness.breach', "
            "severity='warning'. "
            "6. Return {checked: N, passed: N, failed: N, breaches: [...]}. "
            "Graceful: if db_conn_json is missing or connection fails, log error and continue. "
            "Import quality_engine._run_freshness and _open_connection."
        ),
        "task_type": "feature",
        "priority": "medium",
        "depends_on_task_id": "ddx-fr-01",
    },
    {
        "id": "ddx-fr-03",
        "title": "[ddx-fr] genesis_config.yaml — register freshness_guardian reflex",
        "description": (
            "Read args/genesis_config.yaml. "
            "Under reflexes: list, add entry:\n"
            "  - name: freshness_guardian\n"
            "    enabled: true\n"
            "    interval_hours: 1\n"
            "    module: tools.genesis.reflexes.freshness_guardian\n"
            "    class: FreshnessGuardianReflex\n"
            "    description: Hourly freshness check for all enabled freshness quality rules\n"
            "If the file uses a different reflex registration pattern, match it exactly."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ddx-fr-02",
    },
    {
        "id": "ddx-fr-04",
        "title": "[ddx-fr] quality.html — Freshness Monitor section",
        "description": (
            "Update tools/dashboard/templates/data_canvas/quality.html. "
            "Add 'Freshness Monitor' section after the score gauge — shows: "
            "Last reflex run timestamp (query dd_freshness_alerts MAX(created_at)), "
            "Count of freshness rules monitored, "
            "Pass/fail indicator per rule: green tick (passed) or red alert (breach) "
            "with latest value and cutoff. "
            "Data loaded via GET /data/api/quality/freshness-status (new endpoint). "
            "Add that endpoint to blueprint.py: queries dd_freshness_alerts "
            "GROUP BY rule_id ORDER BY created_at DESC."
        ),
        "task_type": "feature",
        "priority": "medium",
        "depends_on_task_id": "ddx-fr-03",
    },
    {
        "id": "ddx-fr-05",
        "title": "[ddx-fr] icdev/ mirror + manifest entry for freshness_guardian",
        "description": (
            "Mirror quality.html to icdev/tools/dashboard/templates/data_canvas/quality.html. "
            "Add freshness_guardian.py entry to tools/manifest/genesis.md (or ddc-data-science.md): "
            "Path, Purpose, Reflex class, Run interval, DB interactions "
            "(dd_freshness_alerts write, dd_quality_runs write, genesis_audit write)."
        ),
        "task_type": "chore",
        "priority": "low",
        "depends_on_task_id": "ddx-fr-04",
    },
    {
        "id": "ddx-fr-06",
        "title": "[ddx-fr] V&V — CodeLens + Coherence + E2E Playwright (freshness)",
        "description": (
            "Phase 4 V&V gate. Run in order:\n"
            "1. python tools/analysis/code_lens.py --file tools/genesis/reflexes/freshness_guardian.py --json\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "3. Reflex smoke: python -c \"from tools.genesis.reflexes.freshness_guardian import "
            "FreshnessGuardianReflex; r = FreshnessGuardianReflex(); "
            "print(r.run({}, None))\" — assert dict with 'checked' key returned. "
            "4. E2E Playwright: navigate /data/quality, assert 'Freshness Monitor' section "
            "is present in DOM (getElementById or querySelector). "
            "5. python tools/dx/companion.py --sync --write --json\n"
            "All must pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "ddx-fr-05",
    },

    # ── Epic mcp: MCP Scan Gate ───────────────────────────────────────────────
    {
        "id": "ddx-mcp-01",
        "title": "[ddx-mcp] mcp_scanner.py — scan MCP servers for vulnerability patterns",
        "description": (
            "Create tools/mcp/mcp_scanner.py. "
            "Exports scan_mcp_servers(config_path=None) -> dict. "
            "Reads MCP server list from args/mcp_config.yaml (or .mcp.json / claude_desktop_config.json). "
            "For each server checks:\n"
            "  - unauthenticated_transport: stdio servers without auth token in env\n"
            "  - no_tls: SSE/HTTP servers without https:// endpoint\n"
            "  - wildcard_tool_names: tool names matching '*' or overly broad patterns\n"
            "  - missing_classification: server config missing classification field\n"
            "  - privilege_escalation: tools named 'exec', 'shell', 'run_command', 'eval'\n"
            "Returns {servers_scanned: N, findings: [{server_id, server_name, severity, "
            "check, description, cwe}], high_count: N, medium_count: N, passed: bool}. "
            "passed=True only if high_count==0. "
            "Pure function — no Flask, no DB. Supports --json CLI flag."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": "ddx-fr-06",
    },
    {
        "id": "ddx-mcp-02",
        "title": "[ddx-mcp] coherence_checker.py — add check_mcp_security() gate",
        "description": (
            "Update tools/workflow/coherence_checker.py. "
            "Add function check_mcp_security(fix=False) -> dict. "
            "Calls mcp_scanner.scan_mcp_servers(). "
            "If any HIGH findings: gate fails (returns {passed: False, findings: [...]}). "
            "MEDIUM findings: warning only (does not fail gate). "
            "Integrate into the --all pipeline: add 'mcp_security' to the checks dict. "
            "Output format matches other checker functions: "
            "{check: 'mcp_security', passed: bool, findings: [...], message: str}."
        ),
        "task_type": "feature",
        "priority": "high",
        "depends_on_task_id": "ddx-mcp-01",
    },
    {
        "id": "ddx-mcp-03",
        "title": "[ddx-mcp] security_gates.yaml — add MCP security gate entry",
        "description": (
            "Update args/security_gates.yaml. "
            "Under gates: list, add:\n"
            "  - id: MCP-SEC-001\n"
            "    name: MCP Server Security Gate\n"
            "    description: Blocks on HIGH severity MCP server findings (unauthenticated transport, missing TLS, privilege escalation tools)\n"
            "    severity: HIGH\n"
            "    block_on: [unauthenticated_transport, privilege_escalation]\n"
            "    warn_on: [no_tls, missing_classification]\n"
            "    tool: tools/mcp/mcp_scanner.py\n"
            "    nist_controls: [SC-8, AC-3, AU-12]"
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "ddx-mcp-02",
    },
    {
        "id": "ddx-mcp-04",
        "title": "[ddx-mcp] manifest entry for mcp_scanner + commands.md",
        "description": (
            "Find or create tools/manifest/mcp.md (grep tools/manifest/ for mcp). "
            "Add mcp_scanner.py section: Path, Purpose, Key function (scan_mcp_servers), "
            "Checks performed (6 checks), Return schema, Integration with coherence_checker. "
            "Update docs/reference/commands.md under MCP/Security section:\n"
            "  python -m tools.mcp.mcp_scanner --json   # scan all configured MCP servers"
        ),
        "task_type": "chore",
        "priority": "low",
        "depends_on_task_id": "ddx-mcp-03",
    },
    {
        "id": "ddx-mcp-05",
        "title": "[ddx-mcp] V&V — CodeLens + Coherence gate runs mcp_scanner + E2E",
        "description": (
            "Phase 5 V&V gate. Run in order:\n"
            "1. python tools/analysis/code_lens.py --file tools/mcp/mcp_scanner.py --json\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate "
            "→ assert mcp_security check appears in output. "
            "3. Scanner smoke: python -m tools.mcp.mcp_scanner --json "
            "→ assert exit 0 and JSON contains 'servers_scanned' and 'findings' keys. "
            "4. Gate integration: python -c \"from tools.workflow.coherence_checker import "
            "check_mcp_security; r = check_mcp_security(); print(r['check'])\" "
            "→ assert output is 'mcp_security'. "
            "5. python tools/dx/companion.py --sync --write --json\n"
            "All must pass."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "ddx-mcp-04",
    },

    # ── Epic vv: Final V&V ────────────────────────────────────────────────────
    {
        "id": "ddx-vv-01",
        "title": "[ddx-vv] Final — companion sync + full coherence gate + E2E all pages",
        "description": (
            "Final integration V&V for all DDX extensions. Run:\n"
            "1. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "2. python tools/dx/companion.py --sync --write --json\n"
            "3. Full E2E Playwright: start app, test all 3 DDC data science pages:\n"
            "   /data/explore → profile + analyze flow\n"
            "   /data/query → SQL execution + PII warning\n"
            "   /data/quality → quality rules + freshness monitor\n"
            "4. python tools/awareness/component_indexer.py --scan --json\n"
            "5. python tools/awareness/health_prober.py --run-all --json\n"
            "Assert all checks pass. Update docs/features/phase-ddx-extensions.md."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "ddx-mcp-05",
    },
]

# These tasks are genuinely time-deferred: status 'scheduled' WITH a real
# scheduled_at (NOW) — schedule immediately. Stamp both on every task.
for t in TASKS:
    t["status"] = "scheduled"
    t["scheduled_at"] = NOW

report = create_tasks("ddx", TASKS, strict=False, register_project=False)
print(f"\n{'='*60}")
print(report.summary())
print("Project: ddx | Epics: an, pii, cli, fr, mcp, vv | Tasks: 26")
