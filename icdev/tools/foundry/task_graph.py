# CUI // SP-CTI
"""task_graph.py — ACF canvas contract -> canonical full-canvas epic skeleton (acf-design-02).

The sixth stage of the ACF loop (harvest -> synth -> novelty-gate -> score -> CoD ->
spec_generator -> **task_graph** -> seeder). It turns one ``canvas_contract`` (produced by
``tools/foundry/spec_generator.build_canvas_contract``, acf-design-01) into the ordered list
of atomic Kanban tasks that build that capability end-to-end as a brand-new ICDEV canvas.

The graph is the CANONICAL full-canvas epic skeleton — the same shape proven by
``tools/kanban/seed_sipa_integrity.py`` (SIPA) — parameterized by the contract:

    db     constants -> db/init_db -> register (init_icdev_db + migration + conftest) ->
           append-only + .env + config                                       (4 tasks)
    core   one build task per *domain* module in the contract (those not already owned
           by a dedicated epic below)                                        (0..N tasks)
    engine orchestration -> CLI                                              (2 tasks)
    dash   blueprint -> templates(+icdev mirror + IQE widget) -> app.py register(nav +
           start.md) -> IQE adapter(+_CANVAS_MAP + PATH_CANVAS + /api/iqe-query + seed
           queries) — the 8-component new-canvas completeness gate           (4 tasks)
    mcp    MCP gateway registration                          (1 task, only if needs_mcp)
    reflex Genesis periodic reflex                           (1 task, only if needs_reflex)
    doc    goal workflow + manifest shard + commands.md                      (1 task)
    vv     CodeLens -> Coherence(+ruff+pytest) -> companion sync+health -> Playwright E2E
                                                                             (4 tasks)

**Core-epic ownership.** The brief says "one task per module in contract.modules", but the
db / engine / dash / mcp / reflex epics already own the standard scaffold modules
(``constants.py``, ``db/init_db.py``, the engine, ``blueprint.py``, the MCP adapter, the
reflex). The ``core`` epic therefore materializes only the *remaining* domain-specific
modules, so a module is never built twice. For the stock spec_generator contract (whose
modules are exactly the scaffold ones) ``core`` is empty; for a richer contract that
declares extra modules, ``core`` emits one build task each.

**Conventions (mirrors SIPA / the project Guardrails):**
* Task IDs follow ``f'{slug}-{epic}-{n:02d}'`` (``n`` restarts per epic) so
  ``tools/project/kanban_project_sync.py`` auto-populates ``args/projects.yaml``.
* ``depends_on_task_id`` is a single-parent **linear chain** across the whole graph
  (the engine dispatches a task only when its parent is ``done``/``decomposed``); the
  root task's parent is ``None``. Cross-epic prerequisites read naturally because the
  chain is linear (db -> core -> engine -> dash -> ... -> vv).
* Every **build** task description embeds the project Guardrails (get_connection/RLS,
  constants-derived CHECKs, append-only registration, the 8-component canvas gate) and
  carries the ``integrity_gate`` marker (``[integrity_gate: SIPA self-vet required]`` in
  the text + ``integrity_gate: True`` on the dict) so the dispatcher self-vets the
  ACF-generated code via SIPA (``tools/integrity/engine.py --gate``) before merge.

Public API
----------
    build_task_graph(concept, canvas_contract) -> list[dict]
        Each task = {id, title, description, task_type, priority, depends_on_task_id}
        (build tasks additionally carry integrity_gate=True).

CLI
---
    python tools/foundry/task_graph.py --contract-json '{...}' --json
    python tools/foundry/task_graph.py --concept-json '{...}' --json   # derive contract first
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# ── Epic order (the canonical full-canvas skeleton) ──────────────────────────
EPIC_ORDER: tuple[str, ...] = (
    "db", "core", "engine", "dash", "mcp", "reflex", "doc", "vv",
)

# ── Guardrails embedded into every build task description ─────────────────────
# These are the project house rules a generated build task MUST honor. Kept as one
# string so the same wording lands in every kanban_tasks.description and survives
# into the dispatcher's prompt (the marker also lets the dispatcher self-vet).
_GUARDRAILS = (
    "GUARDRAILS (project house rules — honor all): all DB access via get_connection() "
    "(RLS-aware; stamp tenant_id + classification per row — these are sensitive tables); "
    "every SQL CHECK constraint derived from constants.py (NEVER hardcode an enum in a "
    "CREATE TABLE); append-only tables registered in .claude/hooks/pre_tool_use.py "
    "APPEND_ONLY_TABLES and never UPDATE/DELETE-d; the new-canvas 8-component completeness "
    "gate ships together (1 template + 2 icdev/ mirror + 3 route + 4 backing module + "
    "5 constants + 6 migration + 7 nav/parent link + 8 IQE integration); artifacts carry "
    "CUI // SP-CTI markings; run api_surface_extractor before writing tests."
)

# Textual marker + dict flag so the dispatcher knows to route the resulting code
# through SIPA self-vet (args/security_gates.yaml -> integrity_gate / Foundry Self-Vet).
INTEGRITY_GATE_MARKER = "[integrity_gate: SIPA self-vet required before merge]"


# =========================================================================
# CONTRACT INTROSPECTION (defensive — accept any well-formed contract)
# =========================================================================
def _norm_path(path: Any) -> str:
    return str(path or "").replace("\\", "/")


def _module_epic(path: str) -> str:
    """Which dedicated epic owns a module file (else 'core').

    Used both to skip already-owned modules in the core epic and to locate the
    concrete engine / blueprint / mcp / reflex module path for those epics' tasks.
    """
    p = _norm_path(path).lower()
    if p.endswith("/constants.py") or p.endswith("constants.py"):
        return "db"
    if "db/init_db.py" in p:
        return "db"
    if "genesis/reflexes" in p:
        return "reflex"
    if "mcp" in p:  # mcp_adapter.py / mcp.py / .../mcp/...
        return "mcp"
    if p.endswith("blueprint.py"):
        return "dash"
    if p.endswith("_engine.py") or p.endswith("/engine.py"):
        return "engine"
    return "core"


def _find_module(modules: list[dict], epic: str) -> Optional[str]:
    """First module path owned by ``epic`` (None if the contract declares none)."""
    for m in modules:
        path = _norm_path(m.get("path"))
        if path and _module_epic(path) == epic:
            return path
    return None


def _derive_prefix(contract: dict[str, Any]) -> str:
    """The compact ``tools/<prefix>/`` package name for this canvas.

    Prefer the directory component of a declared module path (the contract's own
    source of truth); fall back to the table-name prefix; last resort re-derive
    deterministically from the slug via spec_generator (keeps both stages in sync).
    """
    for m in contract.get("modules", []) or []:
        parts = _norm_path(m.get("path")).split("/")
        # tools/<prefix>/...  (skip shared homes like genesis where prefix != package)
        if len(parts) >= 3 and parts[0] == "tools" and parts[1] not in ("genesis",):
            return parts[1]
    tables = contract.get("tables", []) or []
    if tables:
        name = str(tables[0].get("name") or "")
        if "_" in name:
            return name.rsplit("_", 1)[0]
    # Last resort: re-derive from the slug exactly as spec_generator would.
    try:
        from tools.foundry.spec_generator import _slugify, _table_prefix

        slug = _slugify(contract.get("slug") or contract.get("title") or "capability")
        return _table_prefix(slug, str(contract.get("title") or slug))
    except Exception:  # noqa: BLE001 - never let prefix derivation break the graph
        return "cap"


def _slug(contract: dict[str, Any]) -> str:
    raw = str(contract.get("slug") or "").strip()
    return raw or "capability"


def _page_route(routes: list[dict], slug: str) -> str:
    return next((r["path"] for r in routes if r.get("kind") == "page"), f"/{slug}")


def _api_route(routes: list[dict], prefix: str) -> str:
    return next(
        (r["path"] for r in routes if r.get("kind") == "api"),
        f"/api/{prefix}/items",
    )


# =========================================================================
# EPIC BUILDERS — each returns ordered specs {epic,title,description,task_type,priority}
# =========================================================================
def _db_epic(slug: str, prefix: str, title: str, tables: list[dict]) -> list[dict]:
    table_names = [t.get("name") for t in tables]
    append_only = [t.get("name") for t in tables if t.get("append_only")]
    table_list = ", ".join(str(n) for n in table_names) or f"{prefix}_items"
    ao_list = ", ".join(str(n) for n in append_only) or "(none)"
    flag = f"ICDEV_{slug.upper().replace('-', '_')}_ENABLED"
    return [
        {
            "epic": "db",
            "task_type": "build",
            "priority": "critical",
            "title": "constants.py — enums driving every SQL CHECK + FEATURE_FLAG",
            "description": (
                f"Create tools/{prefix}/__init__.py (side-effect-free) and "
                f"tools/{prefix}/constants.py for the '{title}' canvas. Define the enumerated "
                "tuples that drive every SQL CHECK constraint in db/init_db.py (status / verdict "
                "/ type / severity sets as the domain requires) plus a sql_in(column, values) "
                f"helper that renders \"col IN ('a','b')\" predicates, and FEATURE_FLAG = "
                f"'{flag}'. constants.py is the SINGLE source of truth: db/init_db.py and the "
                "Python validation surface both import these tuples — never restate an enum."
            ),
        },
        {
            "epic": "db",
            "task_type": "build",
            "priority": "critical",
            "title": "db/init_db.py — PG-first dual schema, RLS tables, idempotent init_db()",
            "description": (
                f"Create tools/{prefix}/db/init_db.py modeled on the PG-first dual-schema pattern "
                "(SCHEMA_PG + SCHEMA_SQLITE via .replace() transforms; backend defaults to "
                f"'postgresql', SQLite fallback). Create the {len(tables)} table(s): {table_list}. "
                "These hold sensitive capability data, so use the RLS-aware "
                "tools.db.storage.get_connection() (NOT get_canvas_connection) and put tenant_id + "
                f"classification columns on EVERY table. Append-only tables: {ao_list}. ALL CHECK "
                "constraints derived from constants.py. init_db() must be idempotent (CREATE TABLE "
                "IF NOT EXISTS) and graceful if the tables already exist."
            ),
        },
        {
            "epic": "db",
            "task_type": "build",
            "priority": "critical",
            "title": "Register tables in init_icdev_db.py + numbered migration + conftest schema",
            "description": (
                f"Wire the schema into the platform. (1) Add the {len(tables)} CREATE TABLE "
                f"statements ({table_list}) to tools/db/init_icdev_db.py (both PG and SQLite paths, "
                "mirroring the constants-derived CHECKs). (2) Add a numbered migration under "
                "tools/db/migrations/ (follow the existing NNN_*.py pattern) creating the tables "
                "idempotently, table-existence handled gracefully. (3) Add the tables to "
                "tests/conftest.py MINIMAL_ICDEV_SCHEMA so SQLite-forced tests see them. Verify "
                "`python tools/db/init_icdev_db.py` runs clean and a fresh pytest collection imports."
            ),
        },
        {
            "epic": "db",
            "task_type": "chore",
            "priority": "high",
            "title": "APPEND_ONLY registration + .env toggle + args config skeleton",
            "description": (
                f"(1) Add the append-only tables ({ao_list}) to APPEND_ONLY_TABLES in "
                f".claude/hooks/pre_tool_use.py. (2) Add {flag}=true to .env and .env.example. "
                f"(3) Create args/{prefix}_config.yaml skeleton with the behavior knobs this canvas "
                "needs (thresholds, toggles, llm_assist.enabled:false by default for air-gap "
                "safety). FORGE: behavior is tuned here, never hardcoded in the engine."
            ),
        },
    ]


def _core_epic(slug: str, prefix: str, core_modules: list[dict]) -> list[dict]:
    """One build task per domain module not owned by a dedicated epic."""
    specs: list[dict] = []
    for m in core_modules:
        path = _norm_path(m.get("path"))
        purpose = str(m.get("purpose") or "domain capability logic")
        fname = path.rsplit("/", 1)[-1] if path else "module.py"
        specs.append(
            {
                "epic": "core",
                "task_type": "build",
                "priority": "high",
                "title": f"{fname} — {purpose}",
                "description": (
                    f"Create {path} for the canvas. Purpose: {purpose}. Implement the deterministic "
                    "domain logic (LLM-assist optional with a deterministic fallback so the path "
                    "stays air-gap safe). Any persistence goes through the RLS-aware "
                    "get_connection() stamping tenant_id/classification; any new enum lives in "
                    f"tools/{prefix}/constants.py and feeds the SQL CHECKs. Land focused unit tests "
                    "(run api_surface_extractor.py --file <module> first)."
                ),
            }
        )
    return specs


def _engine_epic(slug: str, prefix: str, engine_mod: str, page_route: str) -> list[dict]:
    return [
        {
            "epic": "engine",
            "task_type": "build",
            "priority": "critical",
            "title": "engine.py — orchestration wiring the modules into one entrypoint",
            "description": (
                f"Create {engine_mod}: the core orchestration that wires the canvas's modules into "
                "a single public entrypoint (e.g. run()/process(...) -> a result dict with an id + "
                "status), persisting at each step. All DB access via get_connection() with "
                "tenant_id/classification. Deterministic-first; any LLM-assist falls back cleanly. "
                "Tests: an end-to-end happy-path fixture produces the expected persisted rows."
            ),
        },
        {
            "epic": "engine",
            "task_type": "build",
            "priority": "critical",
            "title": "engine.py CLI — argparse __main__ with --json (+ --gate where applicable)",
            "description": (
                f"Add an argparse CLI / __main__ to {engine_mod}: a primary action flag, --json for "
                "machine-readable output, and a --gate flag that exits non-zero on the failure "
                "verdict (so it can wire into CI/pre-merge) where the capability warrants it. Tests "
                "invoke main() and assert exit codes + JSON shape for the happy path and a failure "
                f"path. The page lives at {page_route}."
            ),
        },
    ]


def _dash_epic(
    slug: str, prefix: str, blueprint_mod: str, page_route: str,
    api_route: str, iqe_collections: list[str],
) -> list[dict]:
    flag = f"ICDEV_{slug.upper().replace('-', '_')}_ENABLED"
    bp_factory = f"create_{prefix}_blueprint"
    colls = ", ".join(str(c) for c in iqe_collections) or f"{prefix}_items"
    return [
        {
            "epic": "dash",
            "task_type": "build",
            "priority": "critical",
            "title": f"blueprint.py — {bp_factory}() factory: page routes + JSON API",
            "description": (
                f"Create {blueprint_mod} with {bp_factory}() (returns None when {flag} is false), "
                f"url_prefix='{page_route}', before_request init_db. Page route(s): a list/index page "
                "and a detail page. JSON API: POST {api_route} (create/query) plus GET read "
                "endpoints. Reads run under g.security_context (RLS); writes audit-log. Import-safe "
                "with try/except like the other canvases. Tests hit the routes with the Flask test "
                "client. [Canvas gate components 3 (route) + 4 (backing module).]"
            ),
        },
        {
            "epic": "dash",
            "task_type": "build",
            "priority": "high",
            "title": f"Templates — {prefix}/index.html + detail.html (+ icdev/ mirror, IQE widget)",
            "description": (
                f"Create tools/dashboard/templates/{prefix}/index.html (list/index) and detail.html, "
                "extending base.html with the CUI // SP-CTI banner and dark-mode consistent with "
                "other canvases. In Jinja2 use value|round(0)|int (NEVER '%%.0f'|format). Include "
                "{% include 'includes/iqe_query_widget.html' %} and pass iqe_canvas='" + prefix + "'. "
                f"Mirror BOTH templates to icdev/tools/dashboard/templates/{prefix}/ (or rely on "
                "companion sync). [Canvas gate components 1 (template) + 2 (icdev/ mirror).]"
            ),
        },
        {
            "epic": "dash",
            "task_type": "build",
            "priority": "high",
            "title": "Register blueprint in app.py + nav link (base.html) + start.md Pages line",
            "description": (
                f"(1) Register the blueprint in tools/dashboard/app.py (_CANVAS_DEFS entry "
                f"'{prefix}'/'{flag}'/'tools.{prefix}.blueprint'/'{bp_factory}', and add '{prefix}' to "
                "_CANVAS_DEFAULTS_TRUE if it should default on). (2) Add a nav link in base.html "
                f"pointing to {page_route}. (3) Add {page_route} (and any detail path) to the Pages: "
                "line in .claude/commands/start.md. Verify the dashboard starts with no ImportError "
                f"and {page_route} renders. [Canvas gate component 7 (nav/parent link).]"
            ),
        },
        {
            "epic": "dash",
            "task_type": "build",
            "priority": "high",
            "title": "IQE — adapter + _CANVAS_MAP + PATH_CANVAS + /api/iqe-query + 3 seed queries",
            "description": (
                f"Full IQE integration (8th completeness component): create tools/iqe/adapters/"
                f"{prefix}.py registering read-only collections returning REAL rows ({colls}) — "
                "mirror tools/iqe/adapters/ai_augmentation.py. Add the '" + prefix + "' entry to "
                f"_CANVAS_MAP in tools/dashboard/app.py and a PATH_CANVAS entry "
                f"[/^\\{page_route}/, '{prefix}'] in base.html. Add POST {page_route}/api/iqe-query "
                f"to the blueprint. Add >=3 seed queries in context/iqe/queries/{prefix}/*.iqe. "
                "[Canvas gate component 8 (IQE integration).]"
            ),
        },
    ]


def _mcp_epic(slug: str, prefix: str, mcp_mod: Optional[str]) -> list[dict]:
    target = mcp_mod or f"tools/{prefix}/mcp_adapter.py"
    return [
        {
            "epic": "mcp",
            "task_type": "build",
            "priority": "medium",
            "title": "MCP — register the canvas's tools in the gateway",
            "description": (
                f"Register the canvas in the MCP gateway: add the tool definitions to "
                f"tools/mcp/tool_registry.py and their handlers in tools/mcp/gap_handlers.py "
                f"(adapter at {target}), reusing the security/knowledge MCP server pattern "
                "(deterministic, JSON in / JSON out, no shell). Tests: the registry lists the new "
                "tools and a handler returns a JSON result for a fixture input."
            ),
        },
    ]


def _reflex_epic(slug: str, prefix: str, reflex_mod: Optional[str]) -> list[dict]:
    target = reflex_mod or f"tools/genesis/reflexes/{prefix}_cycle.py"
    return [
        {
            "epic": "reflex",
            "task_type": "build",
            "priority": "medium",
            "title": "Genesis reflex — periodic cycle for the canvas",
            "description": (
                f"Create {target} following an existing reflex (CADENCE_HOURS, run(ctx, conn) -> "
                "{status, ...} dict). Run one capability cycle per interval and dedupe so it does "
                "not re-alert. Register the reflex in ALL THREE places (the daemon-registration "
                "gotcha): REFLEX_NAMES in tools/genesis/daemon.py, the reflex registry, and "
                "genesis config. Use 127.0.0.1 (not localhost) for any probe. Tests: a seeded "
                "trigger produces exactly one expected artifact (e.g. one kanban card)."
            ),
        },
    ]


def _doc_epic(slug: str, prefix: str, title: str) -> list[dict]:
    return [
        {
            "epic": "doc",
            "task_type": "chore",
            "priority": "medium",
            "title": "goal workflow + manifest shard + commands.md registration",
            "description": (
                f"(1) Create goals/{prefix}.md describing the '{title}' orchestration and add a row "
                "to goals/manifest.md. (2) Create the tools/manifest/" + prefix + ".md shard "
                f"documenting every tools/{prefix}/ module + the IQE adapter (+ MCP/reflex if "
                "present) and add its index line to tools/manifest.md. (3) Add the canvas's CLI "
                "commands to docs/reference/commands.md."
            ),
        },
    ]


def _vv_epic(slug: str, prefix: str, page_route: str) -> list[dict]:
    return [
        {
            "epic": "vv",
            "task_type": "test",
            "priority": "high",
            "title": "Comprehensive CodeLens — every module PASSes the quality gate",
            "description": (
                f"Run tools/analysis/code_lens.py --file <m> --json on EVERY module under "
                f"tools/{prefix}/ and on tools/iqe/adapters/{prefix}.py (+ the reflex if present). "
                "Refactor any module that does not PASS (maintainability >= 0.6, zero critical "
                "smells, no high cyclomatic complexity) without changing behavior (tests stay "
                "green). Record before/after gate results in the completion notes."
            ),
        },
        {
            "epic": "vv",
            "task_type": "test",
            "priority": "critical",
            "title": "Comprehensive Coherence — coherence_checker --all --fix --gate + ruff + pytest",
            "description": (
                "Run `python tools/workflow/coherence_checker.py --all --fix --gate` and make it "
                "green for the canvas: schema<->code parity (INSERTs match CREATE TABLE), manifest "
                "(new modules documented), append-only (tables registered), route uniqueness + "
                f"nav/route parity ({page_route}), sandbox coverage, security_context (RLS "
                f"auto-wiring intact), IQE wiring. Then `ruff check tools/{prefix}/` clean and "
                f"`pytest tests/ -k {prefix} -v` green (run api_surface_extractor before fixing tests)."
            ),
        },
        {
            "epic": "vv",
            "task_type": "test",
            "priority": "high",
            "title": "Companion sync (foreground) + health check + full suite green",
            "description": (
                "Run `python tools/dx/companion.py --sync --write --json` IN THE FOREGROUND (never "
                "background — background sync has deleted uncommitted work) so the new templates/"
                "skills propagate to all AI platforms. Run `python tools/testing/health_check.py "
                "--json` (env + DB + deps green). Run the full `pytest tests/ -q` to confirm no "
                "regressions from the new schema. On Windows set $env:PYTHONPATH='C:\\AI\\ICDev'."
            ),
        },
        {
            "epic": "vv",
            "task_type": "test",
            "priority": "critical",
            "title": "Playwright E2E — exercise the canvas + IQE widget + screenshots + feature doc",
            "description": (
                f"End-to-end via Playwright MCP against the running dashboard: open {page_route}, "
                "exercise the primary happy path through the UI, exercise the IQE widget with a "
                "seed query and confirm REAL rows, and capture screenshots to "
                f"playwright/screenshots/{prefix}-*.png. Write docs/features/phase-{slug}-results.md "
                "summarizing verified behavior + any LLM fallbacks. All gates green."
            ),
        },
    ]


# =========================================================================
# PUBLIC: build_task_graph
# =========================================================================
def build_task_graph(
    concept: dict[str, Any], canvas_contract: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build the canonical full-canvas epic skeleton for ``canvas_contract``.

    Args:
        concept: the foundry_concepts-shaped dict the contract was derived from
            (used for the title fallback; the structure comes entirely from the
            contract so the graph is deterministic for a given contract).
        canvas_contract: ``{slug, title, env_flag, tables[], modules[], routes[],
            iqe_collections[], needs_mcp, needs_reflex}`` from spec_generator.

    Returns:
        An ordered list of task dicts, each
        ``{id, title, description, task_type, priority, depends_on_task_id}``;
        build tasks additionally carry ``integrity_gate: True``. IDs are
        ``f'{slug}-{epic}-{n:02d}'``; ``depends_on_task_id`` is a single-parent
        linear chain with the root's parent ``None``. Every returned
        ``depends_on_task_id`` resolves to another task's ``id`` in the list.
    """
    contract = canvas_contract or {}
    concept = concept or {}

    slug = _slug(contract)
    title = str(contract.get("title") or concept.get("name") or slug)
    prefix = _derive_prefix(contract)

    tables = list(contract.get("tables") or [])
    modules = list(contract.get("modules") or [])
    routes = list(contract.get("routes") or [])
    iqe_collections = list(contract.get("iqe_collections") or [])

    page_route = _page_route(routes, slug)
    api_route = _api_route(routes, prefix)

    engine_mod = _find_module(modules, "engine") or f"tools/{prefix}/{prefix}_engine.py"
    blueprint_mod = _find_module(modules, "dash") or f"tools/{prefix}/blueprint.py"
    mcp_mod = _find_module(modules, "mcp")
    reflex_mod = _find_module(modules, "reflex")
    core_modules = [m for m in modules if _module_epic(_norm_path(m.get("path"))) == "core"]

    # Assemble the ordered epic specs.
    specs: list[dict] = []
    specs += _db_epic(slug, prefix, title, tables)
    specs += _core_epic(slug, prefix, core_modules)
    specs += _engine_epic(slug, prefix, engine_mod, page_route)
    specs += _dash_epic(slug, prefix, blueprint_mod, page_route, api_route, iqe_collections)
    if contract.get("needs_mcp"):
        specs += _mcp_epic(slug, prefix, mcp_mod)
    if contract.get("needs_reflex"):
        specs += _reflex_epic(slug, prefix, reflex_mod)
    specs += _doc_epic(slug, prefix, title)
    specs += _vv_epic(slug, prefix, page_route)

    # Assign IDs (per-epic counter) + the single-parent linear chain.
    tasks: list[dict[str, Any]] = []
    prev_id: Optional[str] = None
    epic_counters: dict[str, int] = {}
    for spec in specs:
        epic = spec["epic"]
        n = epic_counters.get(epic, 0) + 1
        epic_counters[epic] = n
        task_id = f"{slug}-{epic}-{n:02d}"

        task_type = spec["task_type"]
        is_build = task_type == "build"
        description = spec["description"]
        if is_build:
            description = f"{description}\n\n{_GUARDRAILS} {INTEGRITY_GATE_MARKER}"

        task: dict[str, Any] = {
            "id": task_id,
            "title": spec["title"],
            "description": description,
            "task_type": task_type,
            "priority": spec["priority"],
            "depends_on_task_id": prev_id,
        }
        if is_build:
            task["integrity_gate"] = True

        tasks.append(task)
        prev_id = task_id

    return tasks


# =========================================================================
# CLI
# =========================================================================
def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACF task-graph templater — canvas_contract -> canonical epic skeleton (CUI // SP-CTI)"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--contract-json", help="A canvas_contract as a JSON object")
    src.add_argument(
        "--concept-json",
        help="A concept as a JSON object (the contract is derived via spec_generator)",
    )
    parser.add_argument("--json", action="store_true", help="Emit the full task list as JSON")
    args = parser.parse_args(argv)

    concept: dict[str, Any] = {}
    if args.contract_json:
        try:
            contract = json.loads(args.contract_json)
        except json.JSONDecodeError as exc:
            parser.error(f"invalid --contract-json: {exc}")
            return 2
    else:
        try:
            concept = json.loads(args.concept_json)
        except json.JSONDecodeError as exc:
            parser.error(f"invalid --concept-json: {exc}")
            return 2
        from tools.foundry.spec_generator import build_canvas_contract

        contract = build_canvas_contract(concept)

    tasks = build_task_graph(concept, contract)

    if args.json:
        print(json.dumps(tasks, indent=2, default=str))
    else:
        print(f"{len(tasks)} tasks for canvas '{contract.get('slug')}':")
        for t in tasks:
            dep = t["depends_on_task_id"] or "(root)"
            gate = " [gate]" if t.get("integrity_gate") else ""
            print(f"  {t['id']:<28} <- {dep:<28} {t['task_type']:<6}{gate}  {t['title']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    _BASE = Path(__file__).resolve().parents[2]
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    raise SystemExit(_main())
