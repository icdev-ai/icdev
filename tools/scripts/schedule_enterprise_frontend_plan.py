# CUI // SP-CTI
"""One-shot, idempotent enqueue for the Enterprise-Frontend + API-First + Air-Gap-Browser plan.

Inserts 9 phases into kanban_tasks with status='scheduled' and wires
depends_on_task_id per the phase graph documented in
C:\\Users\\schuo\\.claude\\plans\\snappy-beaming-journal.md.

Re-runnable: skips phases whose stable id already exists.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "efa-"  # Enterprise Frontend + Api-first + Airgap

PHASES = [
    {
        "id": f"{PREFIX}A-p11-api-v1-namespace",
        "title": "Phase A (P1.1): Centralize /api/v1/* blueprint registration",
        "task_type": "build",
        "priority": "high",
        "depends_on": None,
        "description": (
            "Populate tools/dashboard/api/__init__.py::register_api_blueprints(app). "
            "Mount 55+ blueprints under /api/v1/ prefix; keep /api/* as alias for 1 release. "
            "Refactor tools/dashboard/app.py lines ~1124-1190 to delegate. "
            "Exit gate: codelens --all, coherence_checker --all --fix --gate, "
            "e2e_full_dashboard.py, regression pytest, companion --sync."
        ),
    },
    {
        "id": f"{PREFIX}B-p12-openapi-spec",
        "title": "Phase B (P1.2): Dashboard OpenAPI 3.1 spec + /api/v1/openapi.json + /api/v1/docs",
        "task_type": "build",
        "priority": "high",
        "depends_on": f"{PREFIX}A-p11-api-v1-namespace",
        "description": (
            "tools/dashboard/api/openapi_generator.py patterned from tools/saas/openapi_spec.py. "
            "Walk app.url_map; extract query params; infer JSON response schemas. "
            "Wire GET /api/v1/openapi.json + /api/v1/docs (reuse tools/saas/swagger_ui.py). "
            "Add api-contract-drift gate to args/security_gates.yaml and coherence_checker check. "
            "Exit gate: 5-step phase-exit (codelens/coherence/e2e/regression/companion)."
        ),
    },
    {
        "id": f"{PREFIX}C-p13-jwt-auth",
        "title": "Phase C (P1.3): JWT auth decorator for /api/v1/* frontend consumption",
        "task_type": "build",
        "priority": "high",
        "depends_on": f"{PREFIX}A-p11-api-v1-namespace",
        "description": (
            "tools/dashboard/api/auth.py reusing tools/saas/auth.py JWT logic. "
            "@require_jwt decorator; double-submit CSRF cookie. "
            "No /api/v1/* route may use session['user'] directly. "
            "Exit gate: 5-step phase-exit."
        ),
    },
    {
        "id": f"{PREFIX}D-p31-driver-manager",
        "title": "Phase D (P3.1): Browser driver_manager (Selenium + vendored msedgedriver)",
        "task_type": "build",
        "priority": "high",
        "depends_on": None,
        "description": (
            "FIRST ACTION: grep -r 'from selenium' tools/ tests/ — 58 files already use Selenium "
            "per memory e2e_selenium.md. Refactor, not greenfield. "
            "tools/browser/driver_manager.py singleton: vendored vendor/drivers/msedgedriver/{major}/ "
            "→ Edge (Win11 pre-installed); chromedriver fallback. No runtime downloads. "
            "tools/airgap/driver_vendor.py admin fetcher (SHA256 verify). "
            "Extend tools/airgap/detector.py::has_vendored_driver(). "
            "Exit gate: 5-step phase-exit."
        ),
    },
    {
        "id": f"{PREFIX}E-p41-sharepoint-rest",
        "title": "Phase E (P4.1): SharePoint REST client + ingest (NTLM/Kerberos)",
        "task_type": "build",
        "priority": "high",
        "depends_on": None,
        "description": (
            "tools/sharepoint/client.py SharePointClient over requests + requests-ntlm "
            "(or requests-kerberos) against on-prem /_api/web REST. "
            "tools/sharepoint/ingest.py via get_connection() (never sqlite3.connect per memory). "
            "args/sharepoint.yaml (endpoint, auth mode, credential source). "
            "New tables sharepoint_sites/lists/items/documents (register in "
            ".claude/hooks/pre_tool_use.py only if append-only; likely mutable caches). "
            "Blueprint tools/dashboard/api/sharepoint.py at /api/v1/sharepoint/*. "
            "Exit gate: 5-step phase-exit."
        ),
    },
    {
        "id": f"{PREFIX}F-p42-sharepoint-fallback",
        "title": "Phase F (P4.2): SharePoint Selenium fallback (gated, default off)",
        "task_type": "build",
        "priority": "medium",
        "depends_on": f"{PREFIX}E-p41-sharepoint-rest",
        "description": (
            "tools/sharepoint/browser_fallback.py using driver_manager (Phase D). "
            "tools/sharepoint/selectors.py centralizes DOM selectors for single-file fragility. "
            "Triggered by args/sharepoint.yaml sharepoint.fallback_enabled flag (default off). "
            "Requires both Phase D (driver_manager) and Phase E (REST primary) complete. "
            "Exit gate: 5-step phase-exit."
        ),
    },
    {
        "id": f"{PREFIX}G-p32-e2e-two-tier",
        "title": "Phase G (P3.2): E2E two-tier (schemathesis API + Selenium UI)",
        "task_type": "test",
        "priority": "high",
        "depends_on": f"{PREFIX}B-p12-openapi-spec",
        "description": (
            "Tier A (always runs, air-gap safe): tests/api/ with pytest + schemathesis "
            "property tests off /api/v1/openapi.json. <30s full suite. "
            "Tier B (when driver present): tests/e2e_selenium/ page-object ports from "
            ".claude/commands/e2e/*.md + tests/e2e/*.spec.ts. "
            "Modify tools/testing/e2e_runner.py to add --driver selenium mode. "
            "Keep Playwright 1 release for parity; then retire. "
            "Exit gate: 5-step phase-exit."
        ),
    },
    {
        "id": f"{PREFIX}H-p2-nextjs-scaffold",
        "title": "Phase H (P2): Next.js frontend scaffold + strangler proxy",
        "task_type": "build",
        "priority": "medium",
        "depends_on": f"{PREFIX}C-p13-jwt-auth",
        "description": (
            "npx create-next-app frontend/ (TypeScript, app router, tailwind). "
            "Deps: next, react, @tanstack/react-query, zod, openapi-typescript. "
            "tools/dashboard/proxy.py strangler routing: /api/v1/* → Flask API, "
            "/next/* → Next.js dev server or static export, legacy → Jinja. "
            "Codegen frontend/lib/api-types.ts from /api/v1/openapi.json. "
            "Air-gap path: next export → Flask send_from_directory(frontend/out). "
            "tools/airgap/npm_mirror_sync.py to vendor node_modules/. "
            "Exit gate: 5-step phase-exit."
        ),
    },
    {
        "id": f"{PREFIX}I-post-impl",
        "title": "Phase I: Post-impl mandatory validation + feature docs",
        "task_type": "test",
        "priority": "critical",
        "depends_on": f"{PREFIX}H-p2-nextjs-scaffold",
        "description": (
            "Final gate. Full 5-step validation across the whole plan: "
            "codelens --all --json | coherence_checker --all --fix --gate | "
            "e2e_full_dashboard.py | regression pytest | companion --sync --write --json. "
            "Feature docs at docs/features/phase-N-enterprise-frontend-apifirst-airgap-browser.md. "
            "Walk 8-point registration checklist (CLAUDE.md). Ensure git status clean. "
            "Blocked_by all of A..H (wired via depends_on_task_id chain)."
        ),
    },
]


def enqueue() -> dict:
    conn = get_connection(db_path=str(DB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []
    skipped: list[str] = []
    try:
        cur = conn.cursor()
        for phase in PHASES:
            existing = cur.execute(
                "SELECT id FROM kanban_tasks WHERE id = ?",
                (phase["id"],),
            ).fetchone()
            if existing:
                skipped.append(phase["id"])
                continue
            cur.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "depends_on_task_id, dispatch_source, created_at, updated_at, scheduled_at) "
                "VALUES (?, ?, ?, ?, ?, 'scheduled', ?, 'manual_plan', ?, ?, ?)",
                (
                    phase["id"],
                    phase["title"],
                    phase["description"],
                    phase["task_type"],
                    phase["priority"],
                    phase["depends_on"],
                    now,
                    now,
                    now,
                ),
            )
            inserted.append(phase["id"])
        conn.commit()
    finally:
        conn.close()
    return {
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_phases": len(PHASES),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
