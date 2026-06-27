# CUI // SP-CTI
"""Idempotent enqueue for the Enterprise-Frontend + API-First + Air-Gap-Browser plan.

Decomposes 9 phases (A..I) into ~45 atomic subtasks, each sized to fit a single
agent turn (per memory feedback_kanban_phantom.md — batch tasks phantom-complete
96-100% of the time). Every phase ends with an exit-gate subtask that runs the
mandatory 5-step validation (codelens, coherence, e2e, regression, companion).

Subtasks are inserted with status='scheduled' and linear-chained via
depends_on_task_id so the scheduler only unblocks one task at a time.

Re-runnable: skips rows whose stable id already exists.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "efa-"  # Enterprise Frontend + Api-first + Airgap

# Phase-exit gate text reused verbatim in every gate task.
GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) regression pytest: pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json. "
    "If any step fails, stay in_progress and fix within this gate task."
)

# (suffix, title, task_type, priority, description)
# Ordered list. Each subtask's parent = previous suffix in the list.
SUBTASKS: list[tuple[str, str, str, str, str]] = [
    # ───────── Phase A — P1.1 /api/v1/* namespace ─────────
    ("A1-inventory-blueprints", "A1: Inventory 55+ blueprint registrations in app.py:1120-1240",
     "research", "high",
     "Read tools/dashboard/app.py lines 1120-1240. Produce a flat list of every register_blueprint call: "
     "blueprint variable name, source module, any url_prefix, and any conditional guards (airgap, saas, etc.). "
     "Save inventory as a comment block inside tools/dashboard/api/__init__.py. "
     "No code changes yet — pure discovery. Output: markdown table in __init__.py docstring."),
    ("A2-register-api-v1", "A2: Implement register_api_blueprints(app) with /api/v1/ prefix + /api/ alias",
     "build", "high",
     "Populate tools/dashboard/api/__init__.py::register_api_blueprints(app). Import all blueprints from "
     "the inventory (A1). Mount each under /api/v1/; also register an alias at /api/ (deprecation shim). "
     "Preserve all conditional guards verbatim (airgap, saas, tenant-scope). Do NOT touch app.py yet."),
    ("A3-refactor-app-py", "A3: Refactor app.py to call register_api_blueprints(app)",
     "build", "high",
     "In tools/dashboard/app.py, replace the block at ~lines 1120-1240 with a single call to "
     "register_api_blueprints(app) imported from tools.dashboard.api. Keep the conditional guards "
     "and any non-blueprint side effects (ckg_bp, child-app loops) untouched."),
    ("A-gate", "A-gate: Phase A exit validation", "test", "high", GATE),

    # ───────── Phase B — P1.2 OpenAPI 3.1 ─────────
    ("B1-read-saas-pattern", "B1: Read tools/saas/openapi_spec.py as pattern reference",
     "research", "high",
     "Read tools/saas/openapi_spec.py end-to-end. Write a ~40-line summary at the top of "
     "tools/dashboard/api/openapi_generator.py explaining how the SaaS generator builds schemas, "
     "which helpers (if any) we'll reuse vs. re-implement, and the diff from 3.0.3 → 3.1."),
    ("B2-openapi-skeleton", "B2: openapi_generator.py skeleton — walk app.url_map",
     "build", "high",
     "Create tools/dashboard/api/openapi_generator.py with generate_openapi_spec(app) that walks "
     "app.url_map and emits a minimal OpenAPI 3.1 dict (info, paths stubs, empty components). "
     "Iterate only /api/v1/* routes. Return {'openapi': '3.1.0', 'info': {...}, 'paths': {...}}."),
    ("B3-query-param-schemas", "B3: Extract query params + infer response schemas",
     "build", "high",
     "Extend openapi_generator.py: for each /api/v1/* route, parse request.args access patterns from "
     "the handler source (ast module), map to OpenAPI parameters[]. For responses, hit each GET route "
     "against a live dev server (port 5050) with a 2s timeout and infer schema from the sample payload "
     "using genson (or a minimal homegrown inferrer). Skip POST/DELETE response-sampling."),
    ("B4-wire-openapi-routes", "B4: Wire /api/v1/openapi.json + /api/v1/docs (reuse swagger_ui.py)",
     "build", "high",
     "Add two routes to tools/dashboard/api/__init__.py (or a dedicated meta.py blueprint): "
     "GET /api/v1/openapi.json returns generate_openapi_spec(current_app) cached for 5 min; "
     "GET /api/v1/docs renders Swagger UI pointing at /api/v1/openapi.json "
     "(reuse tools/saas/swagger_ui.py — import, don't copy)."),
    ("B5-api-drift-gate", "B5: api-contract-drift gate in args/security_gates.yaml",
     "build", "medium",
     "Add a new gate entry named api-contract-drift to args/security_gates.yaml. Severity: warning. "
     "Condition: coherence_checker finds any /api/v1/* route not present in the generated OpenAPI spec, "
     "or any OpenAPI path not backed by a live route."),
    ("B6-coherence-check", "B6: coherence_checker check for OpenAPI ↔ route parity",
     "build", "medium",
     "In tools/workflow/coherence_checker.py, add a new check function check_openapi_parity() that "
     "calls generate_openapi_spec(app) and diffs against app.url_map. Register it in the --all runner. "
     "Gate-blocking at severity=error when the api-contract-drift gate is tripped."),
    ("B-gate", "B-gate: Phase B exit validation", "test", "high", GATE),

    # ───────── Phase C — P1.3 JWT auth ─────────
    ("C1-read-saas-auth", "C1: Read tools/saas/auth.py JWT implementation",
     "research", "high",
     "Read tools/saas/auth.py in full. Note: token issuance flow, secret source (env var vs config), "
     "algorithm (expect RS256/HS256), claim set, expiry policy, refresh flow if any. "
     "Write findings as a docstring preamble inside tools/dashboard/api/auth.py stub."),
    ("C2-require-jwt-decorator", "C2: Implement @require_jwt + JWT issuance endpoint",
     "build", "high",
     "Create tools/dashboard/api/auth.py with @require_jwt decorator (reuse SaaS decode_token helper). "
     "Add POST /api/v1/auth/token endpoint accepting username+password (dev) or API key (prod), "
     "returning a short-lived JWT. Short-lived = 15 min access + 7 day refresh."),
    ("C3-csrf-double-submit", "C3: Double-submit CSRF cookie helper",
     "build", "medium",
     "In tools/dashboard/api/auth.py add csrf_protect() helper: set csrftoken cookie on GET, "
     "validate X-CSRF-Token header matches cookie on state-changing methods (POST/PUT/DELETE). "
     "Only enforced when request comes from same-origin browser (Origin/Referer check)."),
    ("C4-sweep-apply-jwt", "C4: Apply @require_jwt to all /api/v1/* routes",
     "build", "high",
     "Sweep all 55+ blueprint handlers under tools/dashboard/api/*.py and decorate POST/PUT/DELETE "
     "routes with @require_jwt + @csrf_protect. GET routes get @require_jwt only. "
     "Exceptions: /api/v1/auth/token, /api/v1/openapi.json, /api/v1/docs (document in skip-list)."),
    ("C-gate", "C-gate: Phase C exit validation", "test", "high", GATE),

    # ───────── Phase D — P3.1 driver_manager ─────────
    ("D1-grep-selenium", "D1: Inventory 58 existing Selenium call sites",
     "research", "high",
     "Run grep -rn 'from selenium' tools/ tests/ and produce a flat list of (file, line, call_pattern). "
     "Group by pattern: webdriver.Chrome(options=...), webdriver.Edge(...), webdriver.Firefox(...). "
     "Save list to tools/browser/README.md (new file) as a refactor inventory."),
    ("D2-driver-manager-api", "D2: Design tools/browser/driver_manager.py public API",
     "research", "high",
     "Design the get_driver() singleton signature and document in tools/browser/driver_manager.py "
     "docstring only (no implementation yet): parameters (browser=auto, headless=True, window_size), "
     "return type (webdriver.Edge | webdriver.Chrome), side effects (caches instance per-thread), "
     "cleanup contract (call-site responsibility via context manager OR atexit)."),
    ("D3-driver-manager-impl", "D3: Implement driver_manager.py with vendored-driver resolution",
     "build", "high",
     "Implement get_driver() in tools/browser/driver_manager.py. Resolution order: "
     "(a) vendor/drivers/msedgedriver/{major_version}/msedgedriver.exe if Edge installed; "
     "(b) vendor/drivers/chromedriver/{major}/chromedriver.exe if Chrome installed; "
     "(c) raise AirgapDriverMissingError with admin-refresh instructions. No CDN downloads, ever. "
     "Apply headless + air-gap-safe prefs (no telemetry, disable-default-search, no-first-run)."),
    ("D4-vendor-drivers-readme", "D4: vendor/drivers/README.md + directory structure",
     "chore", "medium",
     "Create vendor/drivers/README.md documenting: directory layout (msedgedriver/{major}/, "
     "chromedriver/{major}/), SHA256 manifest file format, refresh workflow, Edge version policy "
     "(keep N and N-1 major), and security warning about checking binary SHA before commit."),
    ("D5-driver-vendor-script", "D5: tools/airgap/driver_vendor.py admin fetcher",
     "build", "medium",
     "Create tools/airgap/driver_vendor.py --browser edge --version 131 --verify. Fetches msedgedriver "
     "from the official Microsoft endpoint, SHA256-verifies against a pinned manifest (docs/security/"
     "driver-hashes.md — new file), drops into vendor/drivers/msedgedriver/131/. Admin-only; never called "
     "at runtime. Refuses to run when is_airgap() returns True."),
    ("D6-has-vendored-driver", "D6: Extend tools/airgap/detector.py with has_vendored_driver()",
     "build", "low",
     "Add has_vendored_driver() -> dict to tools/airgap/detector.py returning "
     "{'edge': bool, 'chrome': bool, 'path': str|None, 'version': str|None}. "
     "Used by driver_manager.D3 for fast-path and by health_check.py."),
    ("D7-pilot-refactor", "D7: Pilot refactor — migrate 3 existing test files to get_driver()",
     "build", "medium",
     "Pick 3 representative files from D1 inventory (one simple, one complex, one canvas-specific). "
     "Replace direct webdriver.Chrome(options=...) with: from tools.browser.driver_manager import get_driver. "
     "Keep all test assertions identical. Run the 3 files to verify parity before moving on."),
    ("D-gate", "D-gate: Phase D exit validation", "test", "high", GATE),

    # ───────── Phase E — P4.1 SharePoint REST ─────────
    ("E1-sharepoint-yaml", "E1: args/sharepoint.yaml config schema",
     "build", "high",
     "Create args/sharepoint.yaml with keys: endpoint_url, auth_mode (ntlm|kerberos|basic), "
     "credential_source (env|windows_sso|vault), site_scope (list of site URLs), "
     "fallback_enabled (bool, default false), ingest_interval_sec. "
     "Document each key with inline comments. Load via tools/config_loader.py pattern."),
    ("E2-sharepoint-client", "E2: tools/sharepoint/client.py SharePointClient",
     "build", "high",
     "Create tools/sharepoint/client.py: SharePointClient(endpoint, auth_mode). Methods: "
     "list_sites(), get_lists(site), get_list_items(site, list_id), get_file(site, path), search(q). "
     "Use requests + requests-ntlm4 (or requests-kerberos). Hits /_api/web/* on SharePoint Server 2016/2019/SE. "
     "Retry with exponential backoff on 503; hard fail on 401 (auth misconfig)."),
    ("E3-sharepoint-migration", "E3: DB migration 022 — sharepoint_sites/lists/items/documents",
     "build", "high",
     "Create tools/db/migrations/022_sharepoint/up.py + down.py. Tables: sharepoint_sites "
     "(id TEXT PK, url, title, last_modified), sharepoint_lists (id, site_id, name, item_count), "
     "sharepoint_items (id, list_id, title, payload JSONB, modified_at), sharepoint_documents "
     "(id, site_id, path, size, mime_type, content_hash). Use translate_sql-compatible syntax "
     "(write SQLite-style; storage.py handles PG translation)."),
    ("E4-sharepoint-ingest", "E4: tools/sharepoint/ingest.py pipeline",
     "build", "high",
     "Create tools/sharepoint/ingest.py with ingest_all(config) that: (1) loads args/sharepoint.yaml, "
     "(2) instantiates SharePointClient, (3) iterates site_scope, (4) upserts to sharepoint_* tables "
     "via get_connection() (never sqlite3.connect), (5) emits audit event via tools.audit.audit_logger. "
     "CLI: python -m tools.sharepoint.ingest --site URL [--dry-run] --json."),
    ("E5-sharepoint-blueprint", "E5: tools/dashboard/api/sharepoint.py blueprint",
     "build", "medium",
     "Create blueprint with routes: GET /sharepoint/sites, GET /sharepoint/lists?site=, "
     "GET /sharepoint/items?list=, POST /sharepoint/ingest/trigger (admin-only). Register "
     "in tools/dashboard/api/__init__.py inventory. All routes behind @require_jwt from Phase C."),
    ("E6-conftest-schema", "E6: Register sharepoint_* schemas in tests/conftest.py",
     "build", "low",
     "Add the 4 new table DDLs to MINIMAL_ICDEV_SCHEMA in tests/conftest.py so pytest fixtures "
     "can create them in the test DB. Mirror the shapes from the 022 migration."),
    ("E-gate", "E-gate: Phase E exit validation", "test", "high", GATE),

    # ───────── Phase F — P4.2 SharePoint fallback ─────────
    ("F1-sharepoint-selectors", "F1: tools/sharepoint/selectors.py centralized DOM selectors",
     "build", "medium",
     "Create tools/sharepoint/selectors.py with LIST_ITEMS_TABLE, DOCUMENT_LINK_ROW, AUTH_REDIRECT_FORM, "
     "etc. as module-level constants. One file = one point of failure when DOM drifts. "
     "Document last-verified-against SharePoint Server version in the module docstring."),
    ("F2-browser-fallback", "F2: tools/sharepoint/browser_fallback.py using driver_manager",
     "build", "medium",
     "Create browser_fallback.py with fetch_classic_page(url, selectors). Uses "
     "tools.browser.driver_manager.get_driver() (from Phase D). Returns extracted rows as dicts. "
     "Gated by args/sharepoint.yaml sharepoint.fallback_enabled=true (default false). "
     "Raises FallbackDisabledError when called with the flag off."),
    ("F3-integrate-fallback", "F3: Wire fallback into ingest.py decision tree",
     "build", "medium",
     "In tools/sharepoint/ingest.py, when REST returns 404/empty for a site known to have content, "
     "consult fallback_enabled flag and either skip-with-warning (flag off) or delegate to "
     "browser_fallback.fetch_classic_page (flag on). Log the delegation decision."),
    ("F-gate", "F-gate: Phase F exit validation", "test", "high", GATE),

    # ───────── Phase G — P3.2 E2E two-tier ─────────
    ("G1-schemathesis-setup", "G1: tests/api/conftest.py + schemathesis wiring",
     "build", "high",
     "Create tests/api/conftest.py with a session-scoped fixture that starts the Flask dev server "
     "on port 5051 (test-only), loads the OpenAPI spec from /api/v1/openapi.json, and yields a "
     "schemathesis Schema object. Tear down server on session end."),
    ("G2-contract-tests", "G2: tests/api/test_contract.py — schemathesis property tests",
     "build", "high",
     "Create tests/api/test_contract.py using schemathesis.from_pytest(). One @schema.parametrize() "
     "test function that asserts response status is in documented range, content-type matches, "
     "and response validates against declared schema. Target: <30s full suite."),
    ("G3-page-object-base", "G3: tests/e2e_selenium/pages/base.py page-object pattern",
     "build", "high",
     "Create tests/e2e_selenium/pages/base.py with BasePage class: __init__(driver, base_url), "
     "wait_for_element(selector, timeout=10), navigate(path), screenshot(name) → "
     "playwright/screenshots/<name>.png (per CLAUDE.md guardrail)."),
    ("G4-port-first-3-specs", "G4: Port 3 pilot E2E specs to tests/e2e_selenium/",
     "build", "high",
     "Port dashboard_health, activity_usage, agents_monitoring from .claude/commands/e2e/*.md and "
     "tests/e2e/*.spec.ts to tests/e2e_selenium/test_dashboard_health.py, test_activity_usage.py, "
     "test_agents_monitoring.py. Use page-object base from G3. Use get_driver() from Phase D."),
    ("G5-port-remaining-specs", "G5: Port remaining 11 E2E specs",
     "build", "medium",
     "Port the other 11 specs (chat, skillhub, compliance_artifacts, filesync, finetune, genesis, "
     "knowledge_search, proposal_genesis, research, saas_portal, security_scan_results). "
     "Keep the same naming convention and page-object reuse. Skip ones already present."),
    ("G6-e2e-runner-mode", "G6: Add --driver selenium mode to tools/testing/e2e_runner.py",
     "build", "medium",
     "Extend tools/testing/e2e_runner.py with --driver {native,mcp,selenium}. "
     "selenium mode runs tests/e2e_selenium/ via pytest. Detect driver presence via "
     "tools.airgap.detector.has_vendored_driver(); skip with clear reason if absent. "
     "Preserve existing native/mcp paths untouched."),
    ("G-gate", "G-gate: Phase G exit validation", "test", "high", GATE),

    # ───────── Phase H — P2 Next.js ─────────
    ("H1-scaffold", "H1: Scaffold frontend/ with create-next-app",
     "build", "medium",
     "Run: npx create-next-app@latest frontend --typescript --app --tailwind --eslint "
     "--no-src-dir --import-alias '@/*'. Verify frontend/ builds with npm run build. "
     "Add frontend/ to .gitignore for build artifacts (.next/, out/, node_modules/)."),
    ("H2-openapi-codegen", "H2: openapi-typescript codegen → frontend/lib/api-types.ts",
     "build", "medium",
     "Install openapi-typescript as devDependency in frontend/. Add npm script "
     "'codegen': 'openapi-typescript http://localhost:5050/api/v1/openapi.json -o lib/api-types.ts'. "
     "Run once; commit generated file. Document that this runs on every build."),
    ("H3-nav-shell", "H3: Shared nav shell + layout components",
     "build", "medium",
     "Create frontend/app/layout.tsx with global shell: sidebar nav (links to /next/canvas, "
     "/next/kanban, /next/sharepoint), header with CUI banner (per ICDEV guardrail), auth state chip. "
     "Use Radix primitives. Tailwind utility classes only (no custom CSS files)."),
    ("H4-port-index-page", "H4: Port dashboard index to frontend/app/page.tsx",
     "build", "medium",
     "Port tools/dashboard/templates/index.html (56KB) to frontend/app/page.tsx. Use react-query "
     "hooks calling /api/v1/* endpoints via typed client from H2. Preserve the widget grid layout. "
     "Scope: visual parity, not feature parity — follow-up subtasks migrate widgets individually."),
    ("H5-proxy-middleware", "H5: tools/dashboard/proxy.py strangler routing",
     "build", "high",
     "Create tools/dashboard/proxy.py WSGI middleware: /api/v1/* → Flask API (pass through); "
     "/next/* → Next.js dev server (http://localhost:3000) in dev, static from frontend/out/ in prod; "
     "legacy paths → existing Flask routes. Mount in app.py via app.wsgi_app wrap."),
    ("H6-airgap-export", "H6: next export path for air-gap + Flask send_from_directory",
     "build", "medium",
     "In frontend/next.config.js, enable `output: 'export'` for air-gap builds. Add npm script "
     "'build:airgap': 'next build && next export -o out/'. In tools/dashboard/app.py, register "
     "a static route /next/<path> that calls send_from_directory('../frontend/out', path) when "
     "is_airgap() is true."),
    ("H7-npm-mirror-sync", "H7: tools/airgap/npm_mirror_sync.py",
     "build", "medium",
     "Create tools/airgap/npm_mirror_sync.py --source=frontend/node_modules --dest=vendor/npm/. "
     "Tars node_modules and cached tarballs on a connected machine. Documents the .npmrc "
     "registry-override pattern for air-gap rebuilds."),
    ("H-gate", "H-gate: Phase H exit validation", "test", "high", GATE),

    # ───────── Phase I — Post-impl ─────────
    ("I1-feature-doc", "I1: docs/features/phase-N-enterprise-frontend-apifirst-airgap-browser.md",
     "chore", "high",
     "Write feature doc covering: (a) why we moved off Flask-only to Next.js strangler, "
     "(b) the /api/v1/* + OpenAPI contract, (c) the air-gap browser automation story "
     "(vendored msedgedriver, no CDN), (d) SharePoint REST primary + Selenium fallback architecture, "
     "(e) two-tier E2E. Screenshots: playwright/screenshots/<name>.png per CLAUDE.md."),
    ("I2-registration-checklist", "I2: 8-point registration checklist sweep",
     "chore", "high",
     "Walk the 8-point new-tool checklist from CLAUDE.md for every new module shipped in A..H: "
     "(1) tools/manifest/<topic>.md shards, (2) commands.md, (3) security_gates.yaml, "
     "(4) MCP registry+gap_handlers, (5) .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES (none new for us), "
     "(6) tests/conftest.py MINIMAL_ICDEV_SCHEMA, (7) companion --sync --write, (8) coherence --all --fix --gate."),
    ("I3-full-codelens-coherence", "I3: Full CodeLens + coherence across every changed file",
     "test", "high",
     "Run tools/code_intelligence/codelens.py --all --json and tools/workflow/coherence_checker.py "
     "--all --fix --gate across the full repo. Attach JSON outputs to the feature doc from I1. "
     "Fix any new warnings surfaced."),
    ("I4-full-e2e-regression", "I4: Full-dashboard E2E + regression pytest suite",
     "test", "critical",
     "Run tools/testing/e2e_full_dashboard.py + pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium "
     "+ pytest tests/e2e_selenium/ (requires vendored driver from Phase D). Zero new failures vs. "
     "pre-A baseline. Any regression blocks I5."),
    ("I5-companion-sync-final", "I5: Final companion sync + git status clean check",
     "chore", "high",
     "Run tools/dx/companion.py --sync --write --json to propagate changes to all AI platform configs. "
     "Run git status and confirm no unexpected files. Memory feedback_no_marketplace_commit.md: never "
     "commit marketplace/child-app artifacts. Final rubric: the plan is done only when I5 passes."),
]


def enqueue() -> dict:
    conn = get_connection(db_path=str(DB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []
    skipped: list[str] = []
    parent: str | None = None
    try:
        cur = conn.cursor()
        for suffix, title, task_type, priority, description in SUBTASKS:
            tid = PREFIX + suffix
            existing = cur.execute(
                "SELECT id, status FROM kanban_tasks WHERE id = %s", (tid,),
            ).fetchone()
            if existing:
                skipped.append(tid)
                parent = tid  # preserve chain even when skipping
                continue
            cur.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "depends_on_task_id, dispatch_source, created_at, updated_at, scheduled_at) "
                "VALUES (%s, %s, %s, %s, %s, 'scheduled', %s, 'manual_plan', %s, %s, %s)",
                (tid, title, description, task_type, priority, parent, now, now, now),
            )
            inserted.append(tid)
            parent = tid
        conn.commit()
    finally:
        conn.close()
    return {
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_subtasks": len(SUBTASKS),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
