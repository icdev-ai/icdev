"""Seed ACF (Autonomous Capability Foundry) completion tasks.

Decomposes the plan in c:/AI/acf.md into atomic, dependency-ordered Kanban tasks
under the existing ``acf-`` prefix.  IDs are chosen to avoid collision with the 51
existing tasks (last was acf-vv-04).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on PYTHONPATH so ``tools.*`` resolves (same pattern as seeders)
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.kanban.task_factory import TaskSpec, create_tasks


def main() -> None:
    specs: list[TaskSpec] = []

    # ---------- Tier 1 — Functional blockers ----------
    specs.append(TaskSpec(
        id="acf-reg-01",
        title="Register foundry blueprint in app.py",
        description=(
            "Add the foundry canvas to _CANVAS_DEFS and _CANVAS_ROUTES in "
            "tools/dashboard/app.py so /foundry is reachable.\n\n"
            "Files to edit: tools/dashboard/app.py (~lines 168 and 2210).\n\n"
            "Acceptance (test/verify plan):\n"
            "1. _CANVAS_DEFS entry: ('foundry', 'ICDEV_FOUNDRY_ENABLED', 'tools.foundry.blueprint', 'create_foundry_blueprint').\n"
            "2. _CANVAS_ROUTES entry: 'foundry': '' (empty prefix — routes already full paths).\n"
            "3. Restart dashboard PID; curl /foundry → 200; existing 13 runs / 4 concepts render.\n"
            "4. Screenshot saved to playwright/screenshots/foundry-200.png.\n"
            "5. e2e test: tools/testing/route_smoke.py includes /foundry and passes."
        ),
        task_type="fix",
        priority="critical",
        tags=["acf", "foundry", "dashboard", "blueprint"],
    ))

    specs.append(TaskSpec(
        id="acf-reg-02",
        title="Fix foundry db/init_db.py — export all 6 tables + _is_pg helper",
        description=(
            "Rewrite tools/foundry/db/init_db.py so the engine becomes importable.\n\n"
            "Acceptance:\n"
            "1. Exports init_db() that CREATE-IF-NOT-EXISTS for all 6 tables:\n"
            "   foundry_signals, foundry_concepts, foundry_specs, foundry_runs,\n"
            "   foundry_tasks_emitted, foundry_outcomes.\n"
            "2. Each table has tenant_id + classification columns (RLS).\n"
            "3. CHECK constraints derived from constants (never hardcoded).\n"
            "4. Dual SCHEMA_PG / SCHEMA_SQLITE.\n"
            "5. Exports _is_pg() helper (engine imports it).\n"
            "6. Keeps init_foundry_db as alias for backward-compat.\n"
            "7. Source missing DDLs from live PG (pg_dump --schema-only foundry_*).\n"
            "8. Cross-check column names against blueprint SELECTs (blueprint.py:141-233) "
            "and engine INSERTs (engine.py ~314, 352-378).\n\n"
            "Verify:\n"
            "- python -c \"from tools.foundry.engine import run_cycle\" imports clean.\n"
            "- python -c \"import tools.foundry.spec_generator\" clean.\n"
            "- POST /api/foundry/run (dry-run) returns 200, not 503."
        ),
        task_type="fix",
        priority="critical",
        tags=["acf", "foundry", "db", "schema", "pg"],
    ))

    specs.append(TaskSpec(
        id="acf-reg-03",
        title="Fix blueprint before_request guard import",
        description=(
            "In tools/foundry/blueprint.py:291, correct the import to use "
            "from tools.foundry.db.init_db import init_db (not init_db as init_foundry_db).\n\n"
            "Files to edit: tools/foundry/blueprint.py.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Import statement matches the actual export name from init_db.py after acf-reg-02.\n"
            "2. Blueprint imports without ImportError at startup.\n"
            "3. Verify via python -c 'import tools.foundry.blueprint' with no exception.\n"
            "4. pytest test_blueprint_import.py passes if one exists."
        ),
        task_type="fix",
        priority="high",
        depends_on_task_id="acf-reg-02",
        tags=["acf", "foundry", "blueprint", "import"],
    ))

    # ---------- Tier 2 — Schema persistence ----------
    tier2_common_dep = "acf-reg-02"

    specs.append(TaskSpec(
        id="acf-sch-01",
        title="Add foundry tables to pg_consolidated.sql",
        description=(
            "Propagate the 6 foundry_* table DDLs into tools/db/schema/pg_consolidated.sql "
            "so fresh installs get schema.\n\n"
            "Files to edit: tools/db/schema/pg_consolidated.sql, tools/db/bootstrap_pg.py.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. All 6 tables present (same DDL as acf-reg-02 SCHEMA_PG).\n"
            "2. Regenerated snapshot WITHOUT BOM (per pg-schema-consolidation note).\n"
            "3. tools/db/bootstrap_pg.py can create a fresh DB with foundry tables.\n"
            "4. Verify via docker exec icdev-postgres pg_dump --schema-only sanity check.\n"
            "5. pytest tests/test_bootstrap_pg.py passes if one exists."
        ),
        task_type="build",
        priority="high",
        depends_on_task_id=tier2_common_dep,
        tags=["acf", "foundry", "pg", "schema", "consolidated"],
    ))

    specs.append(TaskSpec(
        id="acf-mig-01",
        title="Create migration 186_foundry_tables.sql",
        description=(
            "Create tools/db/migrations/186_foundry_tables.sql for existing DBs "
            "(next after 185).\n\n"
            "Files to edit: tools/db/migrations/186_foundry_tables.sql.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Migration number is 186 (sequential after 185).\n"
            "2. Contains CREATE-IF-NOT-EXISTS for all 6 foundry_* tables.\n"
            "3. Applies cleanly on existing PG instance.\n"
            "4. tools/db/migrate.py marks it applied after success.\n"
            "5. Verify by running python tools/db/migrate.py --up and checking migration 186 is recorded."
        ),
        task_type="build",
        priority="high",
        depends_on_task_id=tier2_common_dep,
        tags=["acf", "foundry", "migration", "pg"],
    ))

    specs.append(TaskSpec(
        id="acf-tst-01",
        title="Add foundry tables to conftest MINIMAL_ICDEV_SCHEMA",
        description=(
            "Add the 6 foundry_* tables to tests/conftest.py MINIMAL_ICDEV_SCHEMA "
            "so test envs can bootstrap.\n\n"
            "Acceptance:\n"
            "1. All 6 tables present in MINIMAL_ICDEV_SCHEMA.\n"
            "2. pytest tests/ -v still passes (no schema mismatch).\n"
            "3. tests that import tools.foundry.engine no longer skip due to missing tables."
        ),
        task_type="build",
        priority="high",
        depends_on_task_id=tier2_common_dep,
        tags=["acf", "foundry", "tests", "conftest"],
    ))

    specs.append(TaskSpec(
        id="acf-hoo-01",
        title="Add foundry append-only tables to pre_tool_use.py",
        description=(
            "Update .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES with foundry tables.\n\n"
            "Files to edit: .claude/hooks/pre_tool_use.py.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. foundry_signals added to APPEND_ONLY_TABLES (append-only per design).\n"
            "2. If foundry_runs / foundry_outcomes are also append-only, include them.\n"
            "3. Coherence gate passes: python tools/workflow/coherence_checker.py --all --fix --gate exits 0.\n"
            "4. Verify via grep APPEND_ONLY_TABLES pre_tool_use.py shows foundry tables."
        ),
        task_type="build",
        priority="medium",
        depends_on_task_id=tier2_common_dep,
        tags=["acf", "foundry", "hooks", "append-only", "sandbox"],
    ))

    # ---------- Tier 3 — Completeness-gate components ----------
    specs.append(TaskSpec(
        id="acf-nav-01",
        title="Mirror foundry templates to icdev/tools/dashboard/templates/foundry/",
        description=(
            "Copy tools/dashboard/templates/foundry/{index,detail}.html into "
            "icdev/tools/dashboard/templates/foundry/ (package mirror).\n\n"
            "Files to create: icdev/tools/dashboard/templates/foundry/index.html, "
            "icdev/tools/dashboard/templates/foundry/detail.html.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Both index.html and detail.html exist under icdev/ mirror.\n"
            "2. No Jinja2 syntax errors at import time.\n"
            "3. Companion sync can reach them.\n"
            "4. Verify via python -c 'import jinja2; env=jinja2.Environment(loader=jinja2.FileSystemLoader(\"icdev/tools/dashboard/templates/foundry\")); env.get_template(\"index.html\")' with no exception."
        ),
        task_type="build",
        priority="medium",
        depends_on_task_id="acf-reg-01",
        tags=["acf", "foundry", "templates", "icdev", "mirror"],
    ))

    specs.append(TaskSpec(
        id="acf-nav-02",
        title="Mirror foundry IQE adapter to icdev/tools/iqe/adapters/foundry.py",
        description=(
            "Copy tools/iqe/adapters/foundry.py into icdev/tools/iqe/adapters/foundry.py.\n\n"
            "Files to create: icdev/tools/iqe/adapters/foundry.py.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. File exists under icdev/ mirror and is byte-identical to the tools/ original.\n"
            "2. Imports cleanly from icdev.tools.iqe.adapters.foundry.\n"
            "3. Verify via python -c 'from icdev.tools.iqe.adapters.foundry import collections; print(collections)' with no exception.\n"
            "4. pytest tests/test_iqe_foundry_adapter.py passes if one exists."
        ),
        task_type="build",
        priority="medium",
        depends_on_task_id="acf-reg-02",
        tags=["acf", "foundry", "iqe", "icdev", "mirror"],
    ))

    specs.append(TaskSpec(
        id="acf-nav-03",
        title="Add Foundry nav link + PATH_CANVAS mini-bar in base.html",
        description=(
            "Update tools/dashboard/templates/base.html:\n"
            "1. Add Foundry/ACF nav entry near other canvases.\n"
            "2. Add PATH_CANVAS mini-bar entry [/^\\/foundry/, 'foundry'] (~line 895).\n\n"
            "Files to edit: tools/dashboard/templates/base.html.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Foundry link visible in dashboard nav when ICDEV_FOUNDRY_ENABLED=true.\n"
            "2. Mini-bar highlights 'foundry' when on /foundry or /foundry/<id>.\n"
            "3. No 404 from nav click.\n"
            "4. Verify via Playwright screenshot of nav open on /foundry.\n"
            "5. e2e test: tools/testing/route_smoke.py or Playwright nav-click test passes."
        ),
        task_type="build",
        priority="medium",
        depends_on_task_id="acf-reg-01",
        tags=["acf", "foundry", "dashboard", "nav", "ui"],
    ))

    specs.append(TaskSpec(
        id="acf-nav-04",
        title="Add _CANVAS_MAP entry for foundry in app.py",
        description=(
            "Add foundry to the _CANVAS_MAP in tools/dashboard/app.py (~line 3461) "
            "for /api/iqe-query consistency.\n\n"
            "Acceptance:\n"
            "1. Entry like: 'foundry': ('tools.iqe.adapters.foundry', ['foundry.concepts', 'foundry.signals', 'foundry.runs', 'foundry.outcomes']).\n"
            "2. Collection names verified against the adapter.\n"
            "3. Low priority — blueprint's own /foundry/api/iqe-query already serves widget."
        ),
        task_type="build",
        priority="low",
        depends_on_task_id="acf-reg-02",
        tags=["acf", "foundry", "iqe", "app.py"],
    ))

    specs.append(TaskSpec(
        id="acf-nav-05",
        title="Add /foundry to start.md Pages line",
        description=(
            "Update .claude/commands/start.md Pages line to include /foundry.\n\n"
            "Acceptance:\n"
            "1. Pages line contains /foundry.\n"
            "2. Route verifier (which reads MAIN checkout start.md) no longer flags /foundry as unlisted."
        ),
        task_type="build",
        priority="medium",
        depends_on_task_id="acf-reg-01",
        tags=["acf", "foundry", "start.md", "route-verifier"],
    ))

    # ---------- Tier 4 — Hygiene & verification ----------
    tier4_gate_deps = [
        "acf-reg-01", "acf-reg-02", "acf-reg-03",
        "acf-sch-01", "acf-mig-01", "acf-tst-01", "acf-hoo-01",
        "acf-nav-01", "acf-nav-02", "acf-nav-03", "acf-nav-04", "acf-nav-05",
    ]

    # Use the last Tier-3 task as the scalar parent so Tier-4 blocks until all prior work is done.
    specs.append(TaskSpec(
        id="acf-com-01",
        title="Commit all foundry changes to branch",
        description=(
            "Branch off main (if not already on feature), stage all foundry-related edits, "
            "and commit.\n\n"
            "Files to commit: tools/foundry/blueprint.py, tools/foundry/engine.py, "
            "tools/foundry/spec_generator.py, tools/foundry/task_graph.py, "
            "tools/iqe/adapters/foundry.py, plus new/edited files from Tiers 1-3.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. All untracked foundry files committed.\n"
            "2. Commit message follows ICDEV convention.\n"
            "3. Branch is pushable.\n"
            "4. Verify via git status --short shows zero uncommitted foundry changes.\n"
            "5. Verify via git log --oneline -1 shows the commit."
        ),
        task_type="chore",
        priority="high",
        depends_on_task_id="acf-nav-05",
        tags=["acf", "foundry", "git", "commit"],
    ))

    specs.append(TaskSpec(
        id="acf-com-02",
        title="Run coherence_checker --all --fix --gate",
        description=(
            "Run python tools/workflow/coherence_checker.py --all --fix --gate.\n\n"
            "Command: python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Gate passes (exit 0).\n"
            "2. Any auto-fixable issues resolved.\n"
            "3. Unfixable issues documented as follow-up tasks if needed.\n"
            "4. Verify via echo $? returns 0 after the command.\n"
            "5. Screenshot or redirect output to .logs/coherence_acf.log for audit."
        ),
        task_type="chore",
        priority="high",
        depends_on_task_id="acf-com-01",
        tags=["acf", "foundry", "coherence", "gate"],
    ))

    specs.append(TaskSpec(
        id="acf-com-03",
        title="Run companion sync foreground",
        description=(
            "Run python tools/dx/companion.py --sync --write --json in the FOREGROUND.\n\n"
            "Command: python tools/dx/companion.py --sync --write --json\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Sync completes without deleting uncommitted work (commit first!).\n"
            "2. Skills updated for all 10 AI platforms.\n"
            "3. Output saved to .logs/companion_acf.log and reviewed.\n"
            "4. Verify via grep 'sync complete' .logs/companion_acf.log or equivalent.\n"
            "5. Do NOT run in background — it has deleted uncommitted work before."
        ),
        task_type="chore",
        priority="medium",
        depends_on_task_id="acf-com-02",
        tags=["acf", "foundry", "companion", "sync"],
    ))

    specs.append(TaskSpec(
        id="acf-vv-05",
        title="V&V Playwright on /foundry + run cycle end-to-end",
        description=(
            "Genuine V&V that the foundry page works and the engine runs a full cycle.\n\n"
            "Files/artifacts to verify: tools/dashboard/templates/foundry/index.html, "
            "tools/dashboard/templates/foundry/detail.html, tools/foundry/blueprint.py, "
            "tools/foundry/engine.py, playwright/screenshots/foundry-page.png.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Playwright navigates /foundry → screenshot to playwright/screenshots/foundry-page.png.\n"
            "2. Playwright navigates /foundry/<concept_id> → detail page renders.\n"
            "3. POST /api/foundry/run returns 200 and writes a new foundry_runs row.\n"
            "4. No ImportError, no 503, no 404.\n"
            "5. pytest tests/test_foundry_e2e.py passes if one exists.\n\n"
            "This is the real acf-vv-04 / acf-dash-* verification that could not pass before."
        ),
        task_type="test",
        priority="critical",
        depends_on_task_id="acf-com-03",
        tags=["acf", "foundry", "v&v", "playwright", "e2e"],
    ))

    # Optional follow-up
    specs.append(TaskSpec(
        id="acf-nav-06",
        title="Add ACF dashboard home-page card",
        description=(
            "Add a Foundry/ACF summary card to the dashboard home page (index.html) "
            "mirroring the ACE card pattern.\n\n"
            "Files to edit: tools/dashboard/templates/index.html, "
            "icdev/tools/dashboard/templates/index.html.\n\n"
            "Acceptance (test/verify plan):\n"
            "1. Card visible on / when ICDEV_FOUNDRY_ENABLED=true.\n"
            "2. Shows run count, concept count, latest signal count.\n"
            "3. Links to /foundry.\n"
            "4. Mirrored to icdev/tools/dashboard/templates/index.html.\n"
            "5. Verify via Playwright screenshot of / showing the card.\n"
            "6. e2e test: dashboard home page render test passes."
        ),
        task_type="build",
        priority="low",
        depends_on_task_id="acf-vv-05",
        tags=["acf", "foundry", "dashboard", "home", "ui"],
    ))

    report = create_tasks("acf", specs, strict=True)
    print(report.summary())
    if report.validation and not report.validation.ok:
        print("Validation details:")
        for e in report.validation.errors:
            print(f"  - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
