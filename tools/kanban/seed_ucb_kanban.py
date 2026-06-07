# CUI // SP-CTI
"""Seed the UI->CLI Bridge (project ``ucb``) initiative onto the Kanban board.

Decomposes the approved plan
``~/.claude/plans/please-verify-whether-dic-fancy-jellyfish.md`` into 12 atomic,
dependency-ordered tasks across 6 epics (be, widget, roll, dic, audit, vv).

Canonical seeding path — goes through :func:`tools.kanban.task_factory.create_tasks`
(validated, deadlock-safe, idempotent). Never hand-rolls ``INSERT INTO kanban_tasks``.

Run::

    python tools/kanban/seed_ucb_kanban.py --json            # seed
    python tools/kanban/seed_ucb_kanban.py --dry-run --json  # validate only
"""
from __future__ import annotations

import argparse
import json
import sys

from tools.kanban.task_factory import TaskSpec, create_tasks

PROJECT_KEY = "ucb"

TASKS = [
    # ---- Epic be: backend (router override + recorder + shared API) -------- #
    TaskSpec(
        id="ucb-be-01",
        title="Add per-request CLI-bridge override (contextvars) to LLM router",
        task_type="build",
        priority="high",
        description=(
            "Add a module-level `contextvars.ContextVar` named `_cli_bridge_override` "
            "(default None) plus a `cli_bridge_override(enabled: bool)` context manager "
            "to tools/llm/router.py. Update `_cli_bridge_active()` to consult the "
            "ContextVar first and fall back to the ICDEV_CLI_BRIDGE env flag when the "
            "override is None. Use a ContextVar (NOT os.environ mutation or a module "
            "global) because Flask runs threaded and env mutation leaks across "
            "concurrent requests. Acceptance / done when: `with cli_bridge_override(False):` "
            "makes `_cli_bridge_active()` return False even when ICDEV_CLI_BRIDGE=1, the "
            "value resets after the block, and default env behavior is unchanged when no "
            "override is set. Test: add tests/llm/test_cli_bridge_override.py with pytest "
            "cases for override on, off, reset, and env fallback."
        ),
    ),
    TaskSpec(
        id="ucb-be-02",
        title="Record last-served LLM provider on LLMRouter for the status indicator",
        task_type="build",
        priority="high",
        description=(
            "In tools/llm/router.py `invoke()`, inside the existing telemetry block "
            "(where `_log_telemetry` runs), set `LLMRouter._last_served = {provider, "
            "model_id, at}` after a successful provider.invoke(). LLMResponse already "
            "carries `provider` and `model_id` (tools/llm/provider.py) so NO schema "
            "change is needed. Provide a class/staticmethod accessor and a graceful "
            "fallback to the most-recent ai_telemetry row when `_last_served` is cold "
            "(fresh process). Acceptance / done when: after a successful invoke() with a "
            "stub provider, `LLMRouter._last_served['provider']` equals resp.provider and "
            "includes a UTC timestamp. Test: extend tests/llm/test_router*.py with a "
            "stubbed-provider pytest asserting the recorder is populated."
        ),
    ),
    TaskSpec(
        id="ucb-be-03",
        title="Create shared /api/cli-bridge blueprint (prompt + status) and register it",
        task_type="build",
        priority="high",
        depends_on_task_id="ucb-be-01",
        depends_on=["ucb-be-02"],
        description=(
            "Create tools/dashboard/api/cli_bridge_api.py (and mirror to "
            "icdev/tools/dashboard/api/cli_bridge_api.py) exposing POST "
            "/api/cli-bridge/prompt and GET /api/cli-bridge/status. /prompt accepts "
            "{prompt, function?, force_bridge?}, builds an LLMRequest (classification "
            "from g.security_context, 8k char cap, router injection scan), and runs "
            "LLMRouter().invoke() inside cli_bridge_override(force_bridge); returns {ok, "
            "content, provider, model_id, duration_ms, bridge_used}. /status returns "
            "{bridge_enabled, cli_available, last_provider, last_model, last_at}. Register "
            "via `_mount_inline` + ALL_BLUEPRINTS in tools/dashboard/api/__init__.py "
            "(session-cookie + RLS tier, like iqe_api — NOT /api/v1 JWT). Acceptance / "
            "done when: both routes return HTTP 200 with the documented JSON; /prompt "
            "returns {ok:false, cli_unavailable:true} (not 500) when `claude` is absent. "
            "Test: tests/dashboard/test_cli_bridge_api.py using the Flask test client."
        ),
    ),
    TaskSpec(
        id="ucb-be-04",
        title="before_request hook to apply the whole-page CLI-bridge toggle",
        task_type="build",
        priority="medium",
        depends_on_task_id="ucb-be-01",
        description=(
            "Add a Flask before_request hook (in tools/dashboard/app.py or a small "
            "tools/dashboard/api/cli_bridge_api.py helper) that reads the cookie "
            "`icdev_cli_bridge` or header `X-ICDEV-CLI-Bridge` and seeds the router "
            "ContextVar via cli_bridge_override for the whole request lifetime, so EVERY "
            "canvas AI engine endpoint hit during that page honors the per-page toggle "
            "(confirmed whole-page scope). Reset the ContextVar in a teardown/after_request "
            "to avoid leakage. Acceptance / done when: a request carrying cookie "
            "icdev_cli_bridge=off causes any LLMRouter.invoke() during that request to "
            "bypass the bridge even with ICDEV_CLI_BRIDGE=1. Test: add a temporary test "
            "route + pytest in tests/dashboard/test_cli_bridge_api.py asserting the "
            "request-scoped bypass and that state does not leak to the next request."
        ),
    ),
    # ---- Epic widget: shared frontend includes ---------------------------- #
    TaskSpec(
        id="ucb-widget-01",
        title="CLI-bridge status indicator include (navbar pill + per-page toggle)",
        task_type="build",
        priority="high",
        depends_on_task_id="ucb-be-03",
        description=(
            "Create tools/dashboard/templates/includes/cli_bridge_indicator.html (mirror "
            "to icdev/tools/dashboard/templates/includes/) — a navbar-right pill with a "
            "green/amber/grey dot (active+available / enabled-but-CLI-missing / off) plus "
            "the last provider served, and a click popover containing the per-page toggle "
            "('Use local CLI for this page') that sets the icdev_cli_bridge cookie. JS "
            "`icdevCliBridgeRefresh()` (guarded by `if (!window.icdevCliBridgeRefresh)`) "
            "polls GET /api/cli-bridge/status on load and every 30s. Add ONE additive "
            "`{% include %}` line in tools/dashboard/templates/base.html navbar-right "
            "(~line 617). Acceptance / done when: the pill renders on / and on a canvas "
            "page, reflects ICDEV_CLI_BRIDGE state, and the toggle sets the cookie + "
            "updates dot color. Verify with a Playwright snapshot against live :5050."
        ),
    ),
    TaskSpec(
        id="ucb-widget-02",
        title="Interactive CLI-bridge prompt panel include (slide-out)",
        task_type="build",
        priority="high",
        depends_on_task_id="ucb-be-03",
        depends_on=["ucb-widget-01"],
        description=(
            "Create tools/dashboard/templates/includes/cli_bridge_panel.html (mirror to "
            "icdev/tools/dashboard/templates/includes/) by cloning the proven "
            "tools/dashboard/templates/includes/iqe_query_widget.html structure "
            "(collapsible header, textarea, Run button, status line, results area). Global "
            "JS `icdevCliRun(id)` (guarded by `if (!window.icdevCliRun)`) POSTs to "
            "/api/cli-bridge/prompt with {prompt, function, force_bridge:<toggle state>} "
            "and renders data.content plus a footer 'served by {provider}/{model} in "
            "{duration_ms}ms'; the function hint derives from request.path / "
            "window.ROUTE_MODULE_MAP. Add ONE additive `{% include %}` line in base.html "
            "beside the existing assistant_widget include (~line 661) — global, no "
            "per-canvas edits. Acceptance / done when: on /, typing a prompt + Run calls "
            "the endpoint, shows the response + provider footer, and degrades gracefully "
            "when the CLI is absent. Verify with a Playwright run against live :5050."
        ),
    ),
    # ---- Epic roll: global rollout verification ---------------------------- #
    TaskSpec(
        id="ucb-roll-01",
        title="Verify indicator + panel render and function on all canvases",
        task_type="test",
        priority="medium",
        depends_on_task_id="ucb-widget-02",
        depends_on=["ucb-widget-01"],
        description=(
            "Because the two includes are single-injection in "
            "tools/dashboard/templates/base.html there are no per-canvas template edits — "
            "this is a verification sweep, not 26 edits. Iterate every canvas in "
            "`_CANVAS_DEFS` (tools/dashboard/app.py, ~26 canvases: idc, ndc, sdc, bdc, "
            "pdc, odc, ddc, qdc, mdc, aadc, aimc, ohc, iop, mission_canvas, nocc, pmc, "
            "ccc, dsoc, aiify, dic, demo_runner, slides, ace, aisg, integrity) plus the "
            "dashboard home /, confirming the #cli-bridge-indicator pill and the CLI panel "
            "exist and the panel POST succeeds or degrades on each route. Run against the "
            "LIVE dashboard on :5050 (NOT a worktree checkout, which serves stale routes). "
            "Acceptance / done when: a Playwright e2e sweep produces a pass/fail table "
            "covering every canvas route with screenshots saved under "
            "playwright/screenshots/cli-bridge-<canvas>.png."
        ),
    ),
    # ---- Epic dic: DIC runtime re-verify ---------------------------------- #
    TaskSpec(
        id="ucb-dic-01",
        title="Runtime re-verify DIC completeness and CLI-bridge support",
        task_type="test",
        priority="high",
        depends_on_task_id="ucb-widget-02",
        description=(
            "Trust runtime over file lists (the ACE '42/42 done' lesson). Re-verify the "
            "Document Intelligence Canvas (/document-intelligence) at runtime: confirm "
            "tools/document_intelligence/search_engine.py and doc_generator.py call LLMs "
            "via LLMRouter with NO direct anthropic/openai SDK imports; with "
            "ICDEV_CLI_BRIDGE=1, run a DIC AI action (search answer or section generate) "
            "and assert telemetry/response shows provider=='cli'; load every DIC page "
            "(index, collections, search, doc detail, review, generate, acoic, finetune, "
            "snippets, templates, freshness, explorer, handoff, analytics) and exercise a "
            "key API endpoint (POST /document-intelligence/api/search). Acceptance / done "
            "when: all DIC pages load 200, a DIC AI action routes through the CLI bridge, "
            "and the indicator + panel render on /document-intelligence. Capture results "
            "via Playwright e2e against live :5050; file any genuine gap as a follow-up task."
        ),
    ),
    # ---- Epic audit: routing bypass audit + fixes ------------------------- #
    TaskSpec(
        id="ucb-audit-01",
        title="Audit repo for LLM SDK calls that bypass LLMRouter",
        task_type="research",
        priority="medium",
        description=(
            "Produce a deterministic findings report of every direct LLM SDK usage "
            "outside the sanctioned provider modules. Grep tools/ and icdev/ for "
            "`import anthropic`, `from anthropic`, `from openai`, `OpenAI(`, `Anthropic(`, "
            "excluding tools/llm/*_provider.py and the known specialized exceptions "
            "(tools/slides/graphics_generator.py, tools/memory/*embed*). For each hit "
            "record file:line and a verdict of legitimate-provider / specialized-allowed / "
            "bypass-to-fix (a bypass is any canvas/engine AI feature that should route "
            "through LLMRouter so ICDEV_CLI_BRIDGE applies). Acceptance / done when: a "
            "report (markdown or JSON under .tmp/ or docs/) lists every call site with its "
            "verdict and a short rationale. Test/verify: re-running the documented grep "
            "reproduces exactly the listed call sites (no omissions)."
        ),
    ),
    TaskSpec(
        id="ucb-audit-02",
        title="Fix confirmed LLMRouter bypasses found by the audit",
        task_type="fix",
        priority="medium",
        depends_on_task_id="ucb-audit-01",
        description=(
            "For each call site marked bypass-to-fix by ucb-audit-01, refactor it to route "
            "LLM calls through tools/llm/router.py LLMRouter (or document why it is "
            "intentionally exempt, recording the exemption in the appropriate "
            "tools/manifest/ shard). Preserve existing behavior; touch only the offending "
            "call sites (no drive-by refactors). Acceptance / done when: re-running the "
            "ucb-audit-01 grep yields zero unexplained bypasses, every fixed call site has "
            "a pytest asserting the call goes through the router (e.g. via a patched "
            "LLMRouter), and exemptions are listed in the manifest. Test: add/extend "
            "pytest cases under tests/ for each fixed module and run pytest -q on them."
        ),
    ),
    # ---- Epic vv: docs, registration, end-to-end V&V ---------------------- #
    TaskSpec(
        id="ucb-vv-01",
        title="Docs, manifest, companion sync and coherence gate for the bridge",
        task_type="chore",
        priority="medium",
        depends_on_task_id="ucb-be-03",
        depends_on=["ucb-widget-02"],
        description=(
            "Land the registration paperwork for the new API + includes. Add the new "
            "module to tools/manifest/dashboard-api.md and a router-override note to "
            "tools/manifest/llm-providers.md; document the endpoints in "
            "docs/reference/commands.md; write the feature doc "
            "docs/features/phase-N-ui-cli-bridge.md. Run companion sync FOREGROUND only "
            "(`python tools/dx/companion.py --sync --write --json` — never background; it "
            "has deleted uncommitted work) after committing, then "
            "`python tools/workflow/coherence_checker.py --all --fix --gate`. Keep all "
            "manifest edits confined to this task (additive list entries, union-mergeable). "
            "Acceptance / done when: the coherence gate passes, companion sync reports "
            "clean, and all four docs contain the new entries. Verify by re-running the "
            "coherence_checker gate and confirming exit 0."
        ),
    ),
    TaskSpec(
        id="ucb-vv-02",
        title="End-to-end V&V of the UI->CLI bridge across representative canvases",
        task_type="test",
        priority="high",
        depends_on_task_id="ucb-roll-01",
        depends_on=["ucb-be-04", "ucb-widget-02"],
        description=(
            "Final acceptance V&V against the live dashboard on :5050 across home plus "
            "three representative canvases (DIC /document-intelligence, security /security, "
            "aiify /ai-ify). Scenarios: (1) with ICDEV_CLI_BRIDGE=true and `claude` on "
            "PATH, run the CLI panel and assert the footer shows provider=='cli'; (2) flip "
            "the per-page toggle off, re-run a canvas AI action, and assert telemetry shows "
            "a cloud/local provider (bridge bypassed for the whole page); (3) unset "
            "ICDEV_CLI_BRIDGE and confirm the pill is grey but the panel still works via "
            "cloud/local. Acceptance / done when: all three scenarios pass on every target "
            "surface and screenshots are saved under playwright/screenshots/. Exercise the "
            "surfaces served by tools/dashboard/templates/includes/cli_bridge_panel.html "
            "and tools/dashboard/templates/includes/cli_bridge_indicator.html, and add the "
            "e2e spec at tests/dashboard/test_cli_bridge_e2e.py. "
            "Test harness: Playwright e2e; assert on the panel footer text and the status "
            "pill state."
        ),
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the UI->CLI Bridge (ucb) Kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="validate + report, write nothing")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM rubric during validation")
    ap.add_argument("--json", action="store_true", help="emit JSON report")
    args = ap.parse_args()

    report = create_tasks(
        PROJECT_KEY,
        TASKS,
        dry_run=args.dry_run,
        strict=True,
        llm_grade=not args.no_llm,
    )

    if args.json:
        out = {
            "project": PROJECT_KEY,
            "seeded": report.seeded,
            "skipped": report.skipped,
            "healed": report.healed,
            "warnings": report.warnings,
            "dry_run": report.dry_run,
            "validation_ok": (report.validation.ok if report.validation else None),
        }
        print(json.dumps(out, indent=2))
    else:
        print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
