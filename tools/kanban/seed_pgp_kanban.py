# [CUI // SP-CTI]
"""Seed the Kanban board for **PGP — PostgreSQL Primary Remediation**.

Root cause (per project owner): the platform began on SQLite, then moved to
PostgreSQL via a RUNTIME SQL-translation layer (tools/db/storage.py::translate_sql)
instead of a clean migration. That translator is leaky (json_array_length
precedence bug, json_each gap, etc.), the backend default is still 'sqlite', and
get_connection() falls back to SQLite on EVERY call when PG blips — causing
split-brain and the confusion seen across canvases.

Target architecture:
  * PostgreSQL is PRIMARY and the authored SQL dialect at runtime.
  * The translation layer is remediated to a thin, CORRECT safety-net (not
    load-bearing); high-risk call sites move to portable/PG-native SQL.
  * SQLite is a fallback ONLY during INITIALIZATION when PG is unreachable —
    pinned for the process; runtime NEVER silently switches backends.

Canonical seeding path (tools/kanban/SEEDING.md): BACKLOG, validated, via
task_factory.create_tasks.

Run:
    python tools/kanban/seed_pgp_kanban.py --dry-run
    python tools/kanban/seed_pgp_kanban.py
"""

from __future__ import annotations

import argparse

PROJECT_KEY = "pgp"

# Canvases authored SQLite-first whose backend defaults to sqlite (untested PG
# paths — the network canvas's prior state). Verified individually on PG.
_SQLITE_DEFAULT_CANVASES = [
    ("security_canvas", "/security"),
    ("data_canvas", "/data"),
    ("observability_canvas", "/observability"),
    ("infra_canvas", "/infra"),
    ("migration_canvas", "/migration-canvas"),
    ("pipeline", "/pipeline"),
    ("qdc_canvas", "/qdc"),
    ("agentic_ai_canvas", "/agentic-ai"),
    ("aiml_canvas", "/ai-ml"),
    ("boundary_canvas", "/boundary"),
    ("ops_hub", "/ops"),
    ("zta", "/security/zta"),
]


def _task(tid, title, desc, deps=None, priority="high", task_type="build"):
    return {
        "id": tid, "title": title, "description": desc,
        "task_type": task_type, "priority": priority,
        "status": "backlog", "scheduled_at": None,
        "depends_on_task_id": deps,
    }


TASKS: list[dict] = []

# ── Epic RES: backend resolution + init-only SQLite fallback (KEYSTONE) ─────────
TASKS.append(_task(
    "pgp-res-01",
    "PGP: PG-primary backend resolution with SQLite fallback at INITIALIZATION only",
    "File: tools/db/storage.py (get_connection + new resolve_backend())\n\n"
    "Problem: get_connection() reads ICDEV_STORAGE_BACKEND (default 'sqlite') and, in the PG "
    "branch, falls back to SQLite on EVERY call when PG is unreachable. A mid-run PG blip then "
    "silently writes to a DIVERGENT SQLite file (split-brain), and the default backend is wrong.\n\n"
    "Fix: a process-level resolver. At first init, probe PG once; if reachable, pin "
    "backend=postgresql for the process lifetime; if UNREACHABLE AT INIT, pin backend=sqlite "
    "(log a loud WARNING — 'init fallback') and surface it on /health. After the backend is "
    "pinned, get_connection() NEVER silently switches: if the pinned backend is postgresql and a "
    "connection later fails, RAISE (do not write to SQLite). Default ICDEV_STORAGE_BACKEND to "
    "'postgresql'. Repurpose ICDEV_PG_NO_FALLBACK so it controls only the INIT probe.\n\n"
    "Acceptance: with PG up, backend pins to postgresql and a forced runtime PG failure raises "
    "(no SQLite write); with PG down at startup, it pins to sqlite once and logs the fallback.\n"
    "Test: tests/db/test_backend_resolution.py covers pin-on-init, no-runtime-switch (raise), "
    "and init-fallback-when-pg-down (monkeypatched probe).",
    deps=None,
))
TASKS.append(_task(
    "pgp-res-02",
    "PGP: Remove per-canvas sqlite defaults + fix the db_path('.db')->SQLite confusion",
    "Files: tools/<canvas>/db/init_db.py (~12), tools/db/storage.py\n\n"
    "Today ~12 canvases resolve their own *_STORAGE_BACKEND (or ICDEV_CANVAS_STORAGE_BACKEND) to "
    "'sqlite', so they stay on SQLite even on a PG-primary stack; and get_connection() forces "
    "SQLite whenever db_path ends in '.db', while a non-'.db' name (e.g. 'network_canvas') falls "
    "through to the shared PG icdev DB — the exact ambiguity that caused the collisions.\n\n"
    "Fix: canvases inherit the platform backend via the resolver from pgp-res-01 (no hard "
    "'sqlite' default). Define and document ONE policy: on PG, canvas tables live in the shared "
    "icdev database (namespaced to avoid collisions); the '.db'-path SQLite branch is used ONLY "
    "when the process backend pinned to sqlite. Update get_canvas_connection() accordingly.\n\n"
    "Acceptance: with ICDEV_STORAGE_BACKEND unset, every canvas init_db resolves to postgresql "
    "and returns an RLS-disabled PG connection; no canvas hard-codes a sqlite default.\n"
    "Test: tests/db/test_canvas_backend_resolution.py parametrized over all canvas init modules.",
    deps="pgp-res-01",
))

# ── Epic TX: translation remediation (correctness + reduce reliance) ────────────
TASKS.append(_task(
    "pgp-tx-01",
    "PGP: Fix translate_sql correctness gaps (json_array_length precedence + json_each)",
    "File: tools/db/storage.py (translate_sql rules 15/16 + new json_each rule)\n\n"
    "Rule 16 rewrites json_array_length(X) -> jsonb_array_length(X::jsonb) without "
    "parenthesizing X; when X is a translated json_extract (graph_json::jsonb->>'nodes') the "
    "::jsonb cast binds to the key literal -> 'invalid input syntax for type json' (the network "
    "home/project 500s). Fix to jsonb_array_length((X)::jsonb). Also add a json_each rule "
    "(json_each(col,'$.p') -> jsonb_array_elements((col::jsonb)->'p')) which currently has NO "
    "rule and passes through verbatim, breaking on PG.\n\n"
    "Acceptance: translate_sql produces parenthesized, executable PG SQL for nested "
    "json_array_length and for json_each; the 18 existing JSON call sites work on PG.\n"
    "Test: tests/db/test_translate_sql_json.py asserts output + live PG round-trip for both.",
    deps=None,
))
TASKS.append(_task(
    "pgp-tx-02",
    "PGP: Reduce translator reliance — move high-risk runtime SQL to PG-native/portable form",
    "Files: tools/cloud/*, tools/creative/creative_engine.py, tools/dashboard/app.py, "
    "tools/network/network_ingester.py, tools/research/trend_detector.py\n\n"
    "Translation is meant to be a thin safety-net, not load-bearing. Convert the remaining "
    "runtime JSON-SQL call sites (the 18 json_extract/json_array_length occurrences outside the "
    "already-fixed network blueprint) to portable form — either compute in Python (as done for "
    "the network blueprint) or author PG-native jsonb. Establish the convention in CLAUDE.md: "
    "runtime SQL is authored for PG; translate_sql exists only for the SQLite init-fallback.\n\n"
    "Acceptance: no runtime module (excluding init/seed/migrate/tests) relies on translate_sql "
    "to rewrite JSON SQL; pg_portability_linter (pgp-tx-03) reports zero high-severity JSON "
    "findings.\n"
    "Test: per-call-site regression tests where logic changed; portability linter run is clean.",
    deps="pgp-tx-01",
))
TASKS.append(_task(
    "pgp-tx-03",
    "PGP: Build pg_portability_linter.py + baseline (catch SQLite-only / translator-dependent SQL)",
    "File: tools/lint/pg_portability_linter.py (new), extending tools/lint/sqlite3_connect_linter.py\n\n"
    "Scan runtime modules under tools/ for PG-unsafe SQL the translator should not have to "
    "handle at runtime: json_each, nested json_array_length(json_extract(...)), PRAGMA in "
    "runtime executes, and direct sqlite3.connect for RUNTIME data access (not init/seed/"
    "migrate). Emit JSON {file,line,pattern,severity,fix}. Exclude db/init_db.py SQLite "
    "branches, tools/db/seeds/*, tools/db/migrate_*, and tests. Support --baseline allowlist.\n\n"
    "Acceptance: `python tools/lint/pg_portability_linter.py --json` lists offenders and exits "
    "non-zero on high-severity above baseline.\n"
    "Test: tests/lint/test_pg_portability_linter.py catches a seeded json_each and ignores a "
    "seeded init_db SQLite branch.",
    deps="pgp-tx-02",
))

# ── Epic SCH: clean PG schema + data + collisions ──────────────────────────────
TASKS.append(_task(
    "pgp-sch-01",
    "PGP: Canvas-agnostic SQLite->PG data migrator (generalize the network migrator)",
    "File: tools/db/migrate_canvas_to_pg.py (new), from tools/network/db/migrate_sqlite_to_pg.py\n\n"
    "Reusable migrator parameterized by canvas (source data/<canvas>.db + the canvas init "
    "SCHEMA): (1) run each DDL statement in its OWN committed transaction, (2) ENSURE EVERY "
    "SQLite table exists in PG up-front incl. EMPTY ones (auto-create from PRAGMA table_info — "
    "missing empty tables cause UndefinedTable 500s), (3) copy rows idempotently per-row with "
    "ON CONFLICT DO NOTHING, (4) skip immutable audit tables, (5) report per-table + id-type/"
    "collision skips.\n\n"
    "Acceptance: `python -m tools.db.migrate_canvas_to_pg --canvas network --dry-run` matches "
    "the existing network result; --list enumerates canvases.\n"
    "Test: tests/db/test_migrate_canvas_to_pg.py covers auto-create-missing-table + idempotent "
    "re-run (0 copied, 0 errors).",
    deps=None,
))
TASKS.append(_task(
    "pgp-sch-02",
    "PGP: Resolve shared-PG table-name collisions (simulation_results, chat_messages, topologies)",
    "Files: tools/db/schema/pg_consolidated.sql, owning canvas init modules + queries\n\n"
    "On PG every canvas shares the icdev database, so identically-named tables collide. "
    "Discovered: simulation_results (network's id/topology_id/ran_at schema vs a scenario/metric "
    "subsystem's schema), chat_messages (text vs integer id), topologies (shared, compatible). "
    "For each divergent collision: namespace the per-canvas table (e.g. nc_simulation_results) "
    "or confirm a single shared schema, update all queries, and reflect it in the consolidated "
    "PG schema. Until resolved, queries must degrade gracefully (as patched in the network "
    "project pages).\n\n"
    "Acceptance: the collision auditor (pgp-sch-03) reports zero UNRESOLVED divergent collisions; "
    "affected pages render real data (not the graceful-degrade fallback).\n"
    "Test: tests assert each renamed table is queried by exactly one owner; route smoke green.",
    deps="pgp-sch-01",
))
TASKS.append(_task(
    "pgp-sch-03",
    "PGP: Build canvas table-name collision auditor",
    "File: tools/lint/canvas_table_collision_auditor.py (new)\n\n"
    "Parse every canvas SCHEMA / CREATE TABLE, group by table name, and report names defined by "
    ">1 canvas/module whose column sets differ (divergent — must namespace) vs identical "
    "(benign-shared). Output JSON + a markdown summary under docs/. This is the detector that "
    "feeds pgp-sch-02 and the gate.\n\n"
    "Acceptance: report flags simulation_results + chat_messages as divergent with owning "
    "modules and does not flag a genuinely-shared identical table.\n"
    "Test: tests/lint/test_canvas_table_collision_auditor.py on a seeded divergent + identical "
    "pair.",
    deps="pgp-sch-01",
))

# ── Epic GATE: prevent regressions ─────────────────────────────────────────────
TASKS.append(_task(
    "pgp-gate-01",
    "PGP: Wire portability linter + collision auditor into the coherence/security gate",
    "Files: tools/workflow/coherence_checker.py, args/security_gates.yaml, CLAUDE.md\n\n"
    "Add check_pg_portability to coherence_checker that runs pg_portability_linter (pgp-tx-03) "
    "and canvas_table_collision_auditor (pgp-sch-03) and fails on NEW high-severity findings "
    "above the committed baseline. Register a blocking/warning gate in security_gates.yaml and "
    "document the rule in CLAUDE.md guardrails (runtime SQL is PG-authored; SQLite is init-"
    "fallback only).\n\n"
    "Acceptance: `python tools/workflow/coherence_checker.py --all --gate` runs the check; adding "
    "a json_each call fails the gate, removing it passes.\n"
    "Test: tests/workflow/test_coherence_pg_portability.py asserts pass/fail on a fixture.",
    deps="pgp-tx-03",
))

# ── Epic VFY: per-canvas verification on PG ────────────────────────────────────
for i, (mod, route) in enumerate(_SQLITE_DEFAULT_CANVASES, start=1):
    TASKS.append(_task(
        f"pgp-vfy-{i:02d}",
        f"PGP: Verify + harden {mod} on PostgreSQL",
        f"Canvas: {mod} (routes under {route}, db data/{mod}.db)\n\n"
        f"This canvas defaulted to SQLite, so its PG path is untested (the network canvas's prior "
        f"state: info-box zeros, 500s, missing tables, RLS UndefinedColumn). After the central "
        f"fixes (pgp-res-01/02 backend+RLS, pgp-tx-01 translator, pgp-sch-01 migrator) land:\n"
        f"1. Run tools/db/migrate_canvas_to_pg.py --canvas {mod} so all tables exist in PG (incl. "
        f"empty) and dev data is migrated.\n"
        f"2. Smoke every {route}* route on a PG-primary dashboard; capture any 500.\n"
        f"3. Fix residuals: RLS-disable the connection if needed, replace json_each/nested-json "
        f"call sites, resolve any collision flagged by pgp-sch-03.\n\n"
        f"Acceptance: with ICDEV_STORAGE_BACKEND=postgresql, all {route}* pages return 200 (no "
        f"500/UndefinedColumn/UndefinedTable) and list/detail data renders from PG.\n"
        f"Test: route smoke for {route} on PG passes; add a regression test for one fixed query.",
        deps="pgp-res-02",
    ))

# ── Epic CA: child-app generator DB portability ────────────────────────────────
# Generated child apps (tools/builder/) are a SEPARATE surface from in-tree
# canvases: db_init_generator emits get_connection() WITHOUT importing it
# (NameError), uses SQLite-only idioms (conn.executescript, SELECT ... FROM
# sqlite_master), targets a standalone data/<app>.db, and forge_validator never
# checks DB portability — so any child app run on PG breaks.
TASKS.append(_task(
    "pgp-ca-01",
    "PGP: Fix child-app DB-init generator to emit consistent, PG-portable DB code",
    "Files: tools/builder/db_init_generator.py (generate_init_script ~L1712-1965), "
    "tools/builder/child_app_generator.py (step_05 fallback ~L1514-1556)\n\n"
    "The emitted init script imports only sqlite3/argparse/sys/pathlib but calls "
    "get_connection() (NameError at runtime), uses conn.executescript(CORE_SQL) and "
    "SELECT name FROM sqlite_master — both SQLite-only and broken on PG. Fix the generator to "
    "emit code that: (1) imports its connection helper correctly, (2) executes DDL statement-"
    "by-statement (no executescript), (3) lists tables portably (information_schema on PG, "
    "sqlite_master on SQLite) via a helper, (4) follows the platform backend policy from "
    "pgp-res-01 (PG primary, SQLite fallback at INIT only). The inline step_05 fallback must "
    "be fixed identically.\n\n"
    "Acceptance: a freshly generated child app's init script runs clean on BOTH PG and SQLite "
    "(no NameError, no sqlite_master/executescript failure) and creates its tables.\n"
    "Test: tests/builder/test_db_init_generator_portable.py generates a script and executes it "
    "against PG and SQLite fixtures.",
    deps="pgp-res-01",
))
TASKS.append(_task(
    "pgp-ca-02",
    "PGP: Give generated child apps a portable connection helper honoring the backend policy",
    "Files: tools/builder/db_init_generator.py, tools/builder/scaffolder.py, child-app templates\n\n"
    "Generated child apps emit get_connection() but don't ship a connection module that resolves "
    "it. Either (a) scaffold a small tools/db/storage shim into each child app that mirrors the "
    "parent policy (PG primary; SQLite fallback at INIT only; RLS-disabled for app-local tables), "
    "or (b) make the generated code import the parent storage layer explicitly. Decide and "
    "implement one, documented in the child-app README the generator writes.\n\n"
    "Acceptance: a generated child app connects to PG when ICDEV_STORAGE_BACKEND=postgresql and "
    "falls back to its SQLite db ONLY when PG is unreachable at init; runtime never silently "
    "switches.\n"
    "Test: tests/builder/test_child_app_connection.py asserts backend resolution + init-only "
    "fallback in a generated sample.",
    deps="pgp-ca-01",
))
TASKS.append(_task(
    "pgp-ca-03",
    "PGP: Add a DB-portability gate to forge_validator for generated child apps",
    "File: tools/builder/forge_validator.py (validate())\n\n"
    "forge_validator currently only checks that an init_db file EXISTS (~L707/736); it never "
    "checks DB portability. Add a check that scans the generated child app for PG-unsafe DB code: "
    "conn.executescript, SELECT ... FROM sqlite_master, direct sqlite3.connect for runtime "
    "access, unimported get_connection, and json_each / nested json_array_length. Fail the "
    "--gate on any finding so a non-portable child app cannot pass validation.\n\n"
    "Acceptance: forge_validator --gate fails a child app containing executescript/sqlite_master "
    "and passes one using the portable helper from pgp-ca-01/02.\n"
    "Test: tests/builder/test_forge_validator_db_portability.py with a portable + non-portable "
    "fixture app.",
    deps="pgp-ca-01",
))
TASKS.append(_task(
    "pgp-ca-04",
    "PGP: Audit existing generated child apps + child-app templates for SQLite-isms",
    "Files: child_app_registry (DB), tools/builder/templates/* (and any copied canvas templates)\n\n"
    "Enumerate already-generated child apps via child_app_registry and scan child-app TEMPLATES "
    "the generator copies, for the same anti-patterns (executescript, sqlite_master, "
    "json_extract/json_array_length, sqlite3.connect runtime, .db-hardcoded paths). Produce a "
    "remediation report; fix the templates so future generations are clean and flag any shipped "
    "app needing a patch.\n\n"
    "Acceptance: a report lists every affected template/app with severity; templates are "
    "remediated so a regeneration is portable.\n"
    "Test: re-run pg_portability_linter (pgp-tx-03) over tools/builder/templates — zero high-"
    "severity findings.",
    deps="pgp-ca-01",
))
TASKS.append(_task(
    "pgp-ca-05",
    "PGP: End-to-end — generate a sample child app and verify it runs on PostgreSQL",
    "File: docs/features/phase-pgp-child-apps.md (new)\n\n"
    "Generate a representative child app via child_app_generator + forge_validator --gate with "
    "ICDEV_STORAGE_BACKEND=postgresql, run its init_db, exercise its core route(s), and confirm "
    "no sqlite_master/executescript/NameError/UndefinedTable failures. Then repeat with PG "
    "unreachable to confirm the init-only SQLite fallback. Document results + any deferred "
    "follow-ups.\n\n"
    "Acceptance: the generated app initializes and serves on PG; init-only fallback verified; "
    "feature doc written.\n"
    "Test: the generate+init+smoke run is the test; attach output.",
    deps="pgp-ca-03",
    task_type="test",
))

# ── Epic VV: end-to-end verification ───────────────────────────────────────────
TASKS.append(_task(
    "pgp-vv-01",
    "PGP: Full PostgreSQL route smoke across all canvases + residuals report",
    "File: docs/features/phase-pgp-postgres-primary.md (new)\n\n"
    "Run tools/testing/route_smoke.py --all against a PG-primary dashboard and confirm zero 500s "
    "attributable to SQLite-isms (JSON SQL, RLS UndefinedColumn, UndefinedTable, collisions). "
    "Verify the init-only SQLite fallback works (PG-down-at-startup pins sqlite + logs; PG-up "
    "pins postgresql and never silently switches). Record a before/after canvas x route-health "
    "table and any deferred follow-ups; confirm the coherence gate (pgp-gate-01) is green.\n\n"
    "Acceptance: route_smoke --all on PG reports no portability-class 500s; init-fallback "
    "behavior verified; feature doc written.\n"
    "Test: the smoke run + a PG-down-at-init simulation are the tests; attach JSON output.",
    deps="pgp-gate-01",
    task_type="test",
))


def main():
    ap = argparse.ArgumentParser(description="Seed PGP — PostgreSQL Primary Remediation")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--epic", help="seed only tasks whose id is pgp-<epic>-* (e.g. 'ca')")
    args = ap.parse_args()
    tasks = TASKS
    if args.epic:
        prefix = f"pgp-{args.epic}-"
        tasks = [t for t in TASKS if t["id"].startswith(prefix)]
    from tools.kanban.task_factory import create_tasks
    report = create_tasks(
        PROJECT_KEY, tasks,
        dry_run=args.dry_run, strict=True,
        register_project=True, llm_grade=not args.no_llm,
    )
    print(report)


if __name__ == "__main__":
    main()
