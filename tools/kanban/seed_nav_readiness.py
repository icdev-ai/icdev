# CUI // SP-CTI — Seed NAV Dashboard Menu Production Readiness kanban tasks
"""Seed kanban_tasks for the NAV menu-readiness sweep (nav-*).

MANUAL-ONLY project: every task depends on the sentinel gate ``nav-gate-00``
(held ``in_progress`` forever — see tools/kanban/gates.py) so the autonomous
runner never dispatches them. Opus 4.8 sessions implement each task in an
isolated worktree off origin/main, push + PR, no auto-merge.

Audit provenance: three parallel production-readiness audits of the 8 nav menus
(Build, Intelligence, Compliance, Strategos, Platforms, Studio, Updates, More),
2026-07-18. Findings and file:line references are embedded in each description.

Usage:
    python tools/kanban/seed_nav_readiness.py          # seed (idempotent)
"""
from __future__ import annotations

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

GATE_ID = "nav-gate-00"

COMMON = (
    "\n\nWORKFLOW: manual-gated task (never auto-dispatched). Implement in a "
    "dedicated worktree: git worktree add -b feat/<task-id> <path-outside-repo> "
    "origin/main. Commit, push -u, open PR with gh pr create; do NOT auto-merge. "
    "Run ruff check + pytest tests/ -v --tb=short from the repo root before "
    "pushing. Mark done via: python tools/kanban/cli.py --set-status <id> done "
    "(with .env loaded and origin/main freshly fetched)."
)


def _t(tid, title, desc, priority="high", depends=GATE_ID):
    return {
        "id": tid,
        "title": title[:255],
        "description": desc + COMMON,
        "task_type": "build",
        "priority": priority,
        "status": "backlog",
        "depends_on_task_id": depends,
        "dispatch_source": "manual_seed",
    }


TASKS = [
    {
        "id": GATE_ID,
        "title": "MANUAL-MODE GATE — NAV menu readiness (held in_progress forever)",
        "description": (
            "Sentinel gate for the nav-* manual project. Held in_progress FOREVER "
            "so promote_backlog_to_scheduled never dispatches dependents. Never "
            "promote, dispatch, reap, auto-complete, or open a kanban/nav-gate-00 "
            "PR for this task. See tools/kanban/gates.py."
        ),
        "task_type": "chore",
        "priority": "critical",
        "status": "in_progress",
        "depends_on_task_id": None,
        "dispatch_source": "manual_seed",
    },
    # ── Epic sec — Security P0s + RBAC + XSS ──────────────────────────────
    _t(
        "nav-sec-01",
        "NAV: Fix env-key auto-login admin bypass (P0)",
        "tools/dashboard/auth.py:586-594 silently logs in ANY anonymous request "
        "as the bootstrap admin env user whenever ICDEV_DASHBOARD_API_KEY is set "
        "— and _auto_provision_env_key() (auth.py:611-660) writes that key to "
        ".env on first install, so as-installed ALL traffic is admin. Full auth "
        "bypass if the dashboard is exposed beyond localhost.\n"
        "FIX: (1) only honor the env API key when the request actually presents "
        "it (header/param comparison), (2) gate the silent auto-login behind an "
        "explicit opt-in flag ICDEV_DASHBOARD_DEV_AUTOLOGIN=true, default OFF, "
        "(3) keep _auto_provision_env_key for CLI use but its existence must not "
        "authenticate web traffic.\n"
        "TESTS (deny-case mandatory): anonymous request with flag unset -> 401; "
        "request presenting the key -> authenticated; flag set -> current "
        "behavior. Update docs/ops notes for the new flag.",
        priority="critical",
    ),
    _t(
        "nav-sec-02",
        "NAV: Tenant Admin Console fail-open — enforce admin unconditionally (P0)",
        "tools/admin/blueprint.py:32-39 _require_admin() only aborts 403 when "
        "ICDEV_ENFORCE_CANVAS_ACCESS is truthy; default (unset) ALLOWS THROUGH. "
        "Affected mutations: POST /api/admin/tenants/<id>/components, "
        "sso-providers create/delete, api-keys create, and .../erasure (GDPR "
        "delete). Any authenticated user can flip tenant components, add SSO "
        "IdPs, mint tenant API keys, and trigger data erasure.\n"
        "FIX: enforce the admin-role check unconditionally in _require_admin(); "
        "if a local-dev escape hatch is truly needed, make it an explicit "
        "documented opt-OUT env var, never default-open.\n"
        "TESTS: non-admin user on each mutating admin endpoint -> 403 (deny-case "
        "per ABAC lesson); admin -> allowed. Do NOT change platform-wide "
        "ICDEV_ENFORCE_CANVAS_ACCESS semantics for canvases (per PENTA decision) "
        "— only the admin console's use of it.",
        priority="critical",
    ),
    _t(
        "nav-sec-03",
        "NAV: Usage API — remove admin fail-open + scope to user (P0, issue #137)",
        "tools/dashboard/api/usage.py:18,26-28 defines _DEFAULT_USER={role:admin} "
        "and _get_user() falls back to it. None of /api/usage/summary, "
        "/by-provider, /time-series, /totals (lines ~58,81,105,141) apply any "
        "WHERE user_id= filter or @require_role — every authenticated user sees "
        "org-wide token spend/cost, and an unresolved user becomes admin.\n"
        "FIX: remove the _DEFAULT_USER admin fallback (unresolved -> 401); "
        "@require_role('admin') on org-wide aggregates; non-admin queries scoped "
        "to g.current_user id. Keep response shapes stable for the /usage page.\n"
        "TESTS: non-admin sees only own rows; anonymous -> 401; admin sees all. "
        "Close/annotate issue #137 in the PR description.",
        priority="critical",
    ),
    _t(
        "nav-sec-04",
        "NAV: RBAC on HITL approval queue mutations (P1)",
        "tools/workflow_hitl/blueprint.py has ZERO require_role decorators. "
        "POST /workflow/instances/<id>/approve|kickback|escalate|cancel "
        "(~lines 354,398,437,455) only check a user is logged in — any "
        "developer can approve governance gates and is recorded as approver, "
        "defeating HITL segregation-of-duties.\n"
        "FIX: role-gate all instance mutations (reviewer/approver roles per "
        "RBAC_MATRIX in tools/dashboard/auth.py; add workflow_hitl entries). "
        "Approver identity must come from g.current_user, never request body.\n"
        "TESTS: developer role -> 403 on approve; approver role -> allowed; "
        "audit row records the session user.",
    ),
    _t(
        "nav-sec-05",
        "NAV: RBAC on Strategos + ZIG mutations, add RBAC_MATRIX entries (P1)",
        "apps/strategos/blueprint.py has 0 require_role decorators and "
        "RBAC_MATRIX (tools/dashboard/auth.py:364-403) has no strategos keys. "
        "Any authenticated user can HITL-approve (~:510 /hitl/action, :1808 "
        "resolve, :1833 bulk, :1857 delete), approve OPORDs (:2891), and "
        "advance F3EAD targets (:3332). Same pattern on ZIG mutating endpoints "
        "(/api/zig/capabilities/<id> PATCH, /api/zig/assess) which rely on "
        "global auth only.\n"
        "FIX: add @require_role (e.g. isso/pm/admin) to strategos "
        "approve/resolve/delete/generate routes and ZIG mutations; add "
        "strategos + zig entries to RBAC_MATRIX.\n"
        "TESTS: deny-case per gated route (developer -> 403), allowed role -> "
        "200. Do not re-harden other SHX-covered /security internals.",
    ),
    _t(
        "nav-sec-06",
        "NAV: RBAC batch — kanban scheduler, pulse, AISG, foundry mutations (P1)",
        "Mutating endpoints relying on authentication only:\n"
        "- Kanban scheduler pause/resume/build-mode (tools/dashboard/app.py"
        ":1956-1992) — also reads audit 'actor' from request body (spoofable); "
        "derive from g.current_user.\n"
        "- Pulse approve/reject/publish/unpublish/delete (app.py ~7226,7252,"
        "7361,7398,7503).\n"
        "- AISG deploy/save/import/wizard (tools/aisg/blueprint.py ~214,257,276,"
        "330,345,364,379).\n"
        "- Foundry run trigger (tools/foundry/blueprint.py:413).\n"
        "FIX: @require_role('admin','pm') (or role set consistent with "
        "RBAC_MATRIX) on each; actor/author fields from session user.\n"
        "TESTS: deny-case per endpoint; spoofed body actor ignored.",
    ),
    _t(
        "nav-sec-07",
        "NAV: XSS fix batch — known injection sites (P1)",
        "Fix these confirmed sites:\n"
        "1. app.py:3969 prd_html = markdown(raw_md) (python-markdown passes raw "
        "HTML) + templates/intake_prd_view.html:95 '{{ prd_html | safe }}' — "
        "stored XSS from LLM/user PRD. Sanitize with bleach allowlist or "
        "disable raw HTML.\n"
        "2. app.py:3983 json.dumps(raw_md) + intake_prd_view.html:107 '| safe' "
        "inside <script> — '</script>' breakout. Use Jinja |tojson.\n"
        "3. templates/strategos/dat.html:227 '{{ history_json|safe }}' in "
        "<script> — use |tojson.\n"
        "4. templates/strategos/commander.html:229 innerHTML with "
        "${intsum.assessment_snippet} (LLM prose) unescaped; also :204 CCIR "
        "alerts, and airspace.html:449 — use the esc() helper pattern from "
        "bda.html:157-160 or textContent.\n"
        "5. static/js/strategos-chat.js:258 innerHTML = marked.parse(content) "
        "with no sanitizer — vendor DOMPurify locally (NO CDN — air-gap) and "
        "wrap: DOMPurify.sanitize(marked.parse(content)).\n"
        "6. templates/pulse_post.html:233 '{{ post.body_html|safe }}' — "
        "sanitize LLM HTML on ingest.\n"
        "TESTS: payload round-trip tests per site (script tag + </script> "
        "breakout probes render inert).",
    ),
    _t(
        "nav-sec-08",
        "NAV: Systematic |safe + innerHTML XSS sweep with shared escape helper (P2)",
        "Beyond the known sites (nav-sec-07): ~40 templates use |safe and "
        "there are ~2,355 innerHTML assignments with only ~7 templates "
        "defining a local escapeHtml. Sweep prioritized by content source: "
        "LLM/user-content pages first (chat, ask-icdev, ai-observatory, pulse, "
        "strategos panels), then API-data pages. Introduce ONE shared escape/"
        "sanitize helper (static include, e.g. includes/esc.js or a Jinja "
        "macro) and migrate high-risk sites to it; document the rule "
        "(|tojson for script-embedded JSON, esc()/textContent for innerHTML). "
        "Spot-check verified-safe patterns (knowledge_search.html escapeHtml, "
        "CPMP |tojson|safe) need no change. Deliver a findings table in the PR "
        "for sites deliberately left as-is.",
        priority="medium",
    ),
    # ── Epic llm — Dead LLMRouter API ─────────────────────────────────────
    _t(
        "nav-llm-01",
        "NAV: Replace dead router.complete() calls with invoke(fn, LLMRequest) (P1)",
        "LLMRouter exposes ONLY invoke(function_name, LLMRequest) — see "
        "tools/llm/router.py. These shipped call sites invoke a nonexistent "
        ".complete() inside try/except, so their LLM paths are PERMANENTLY "
        "silently dead (fallback text is always served):\n"
        "- tools/workflow_hitl/report_generator.py:254\n"
        "- tools/strategos/predictive_intel_engine.py:358\n"
        "- tools/migration_intelligence/goal_manager.py:173\n"
        "- tools/noc_canvas/mop_generator.py:77\n"
        "- tools/rag/entitlement_rag.py:61,130\n"
        "- tools/mcp/rag_server.py:527,605\n"
        "- tools/conflict_mesh/ml_pattern_engine.py:148\n"
        "- tools/genesis/reflexes/strategos/red_cell.py:242\n"
        "- tools/ai_augmentation/implementations/llm_http_auth.py:170\n"
        "FIX per site: route through router.invoke('<function>', LLMRequest("
        "...)) with an appropriate function name from args/llm_config.yaml "
        "routing (or the cortex_api.complete facade where governance fits "
        "better); keep the deterministic fallback for LLM-unavailable. Mirror "
        "changes to icdev/tools/* twins where they exist (companion sync).\n"
        "TESTS: with a mocked router, assert the LLM path executes and the "
        "response is consumed; fallback still works when invoke raises. "
        "NOTE the prior DIC lesson memory: dic-ai-assist-broken-router-api.",
    ),
    _t(
        "nav-llm-02",
        "NAV: Coherence rule — flag nonexistent LLMRouter methods (P2)",
        "Add a check to tools/workflow/coherence_checker.py that greps runtime "
        "tools/ + apps/ for LLMRouter instances calling .complete( or .chat( "
        "(and any attribute not in the router's public API) and reports "
        "file:line as WARN (FAIL once nav-llm-01 lands). Pattern-match "
        "variable names bound from LLMRouter()/get_router() where feasible; "
        "false-positive-guard: allow cortex_api.complete and provider-SDK "
        ".complete calls. Register the check in the checker's check list and "
        "add a unit test with a synthetic offender file.",
        priority="medium",
    ),
    # ── Epic bld — Build menu (AISG suite) ────────────────────────────────
    _t(
        "nav-bld-01",
        "NAV: /ai-roi + executive-view — real metrics, honest empty states (P1)",
        "tools/aisg/roi_tracker.py:72-83 returns hardcoded 342.5 hrs / $51k "
        "'demo fallback' whenever aisg_roi_events is empty (i.e. default "
        "install), and monthly_trend (:98-106) is ALWAYS hardcoded regardless "
        "of real data. tools/aisg/executive_view.py:35 (same 342.5), :45 "
        "cost_breakdown, :54 agent_activity, :62 maturity_trend all canned. "
        "Executive-facing fabricated numbers.\n"
        "FIX: compute all metrics from aisg_roi_events (and related tables); "
        "when empty render an explicit empty state ('No ROI events recorded "
        "yet') — never demo numbers presented as real; monthly_trend from DB "
        "grouped by month (compute in Python, not SQLite-dialect SQL).\n"
        "TESTS: empty DB -> empty-state payload (no 342.5 anywhere); seeded "
        "events -> computed aggregates match.",
    ),
    _t(
        "nav-bld-02",
        "NAV: pm-view, compliance-view, ai-skills — real data (P1)",
        "tools/aisg/pm_view.py:30 sprint_tasks, :45 velocity_data, :54 "
        "learning_milestones hardcoded. tools/aisg/compliance_view.py:8 "
        "_ATO_SECTIONS, :24 _POAM_ITEMS — CANNED ATO/POA&M content presented "
        "as compliance posture (worst offender: fake compliance data on a "
        "compliance page). tools/aisg/skills_tracker.py:83 _RECOMMENDATIONS, "
        ":129 gaps, :148 team_members hardcoded fallbacks.\n"
        "FIX: pm_view from kanban_tasks/project data; compliance_view from the "
        "real compliance/POA&M tables (tools/compliance/*, poam data) or an "
        "explicit 'sample data' banner if a real source genuinely doesn't "
        "exist yet; skills_tracker from its DB tables with honest empty "
        "states.\n"
        "TESTS: empty DB -> empty/sample-labeled states; seeded data -> real "
        "values; no canned constants reachable in prod paths.",
    ),
    _t(
        "nav-bld-03",
        "NAV: /ai-handoff import stub + /ai-patterns fake deploy (P1)",
        "tools/aisg/blueprint.py:345-352 api_handoff_import returns "
        "{status:ok, imported_for:...} WITHOUT calling any import routine — "
        "fake success. tools/aisg/pattern_registry.py:156-176 deploy_pattern() "
        "only increments deploy_count while the UI implies GovCloud "
        "deployment.\n"
        "FIX: implement a real handoff import (parse the export format "
        "knowledge_handoff.py produces and persist) OR return 501 with an "
        "honest UI message. For patterns: either wire deploy to a real action "
        "or relabel button/copy to 'Track usage' and rename the counter "
        "accordingly.\n"
        "TESTS: import round-trip (export -> import -> data present) or 501 "
        "surfaced in UI; deploy semantics match the label.",
    ),
    _t(
        "nav-bld-04",
        "NAV: Fail-loud error handling on Build pages (P2)",
        "Bare 'except: pass'/except->demo-data across the Build menu makes a "
        "broken DB look like a healthy dashboard: tools/aisg/blueprint.py:240, "
        "tools/aisg/roi_tracker.py:69 (+ executive_view/pm_view/"
        "compliance_view equivalents), /finetune stats (tools/dashboard/"
        "app.py:6848 zeros-on-error), /connector-forge list (app.py:4018 "
        "empty-on-error).\n"
        "FIX: log exceptions (icdev_logger), return an explicit degraded "
        "payload ({error: true, detail}) the templates render as a warning "
        "banner instead of silent zeros/empties.\n"
        "TESTS: simulated DB failure -> degraded banner payload, error "
        "logged; healthy DB unchanged.",
        priority="medium",
    ),
    # ── Epic intel — Intelligence menu ────────────────────────────────────
    _t(
        "nav-intel-01",
        "NAV: Intelligence menu deep audit-and-fix (P1)",
        "Wave-1 audit confirmed routes/templates exist but did NOT deep-audit "
        "backing logic for: /ai-observatory, /ontology, /autoresearch, "
        "/system-graph, /components-map, /skillhub, /writeguard, "
        "/code-quality, and the pulse governance judge/publish path "
        "(fail-open suspicion).\n"
        "DO: audit each backing module for (a) stub/canned data presented as "
        "real, (b) fail-open governance/error handling, (c) dead LLM calls, "
        "(d) unescaped LLM/user content rendering; fix what is small, file "
        "follow-up nav- tasks (via task_factory, depends_on nav-gate-00) for "
        "anything large. Deliver an audit table (page -> verdict -> fix/"
        "follow-up) in the PR description.\n"
        "Known-good reference: /knowledge-search (real data, escaped output).",
    ),
    _t(
        "nav-intel-02",
        "NAV: /slides + /translations pipeline audit-and-fix (P2)",
        "Same audit-and-fix criteria as nav-intel-01 for the Slide Deck "
        "Generator (/slides, tools/slides or tools/viz backing) and "
        "Translations (/translations, tools/translation): stub/canned data, "
        "fail-open, dead LLM calls, unescaped rendering, and whether "
        "generation errors surface honestly in the UI. Fix small issues "
        "in-place; file follow-up nav- tasks for large ones.",
        priority="medium",
    ),
    # ── Epic comp — Compliance menu ───────────────────────────────────────
    _t(
        "nav-comp-01",
        "NAV: FedRAMP readiness 100% on zero scoreable controls (P2)",
        "tools/compliance/fedramp_report_generator.py:325-326: when "
        "scoreable <= 0 (every control not_applicable) the generator returns "
        "100.0, 'Ready for Assessment' — a system with zero scoreable "
        "controls reads as fully ready.\n"
        "FIX: return 0.0 with status 'Insufficient Scope' (or equivalent "
        "explicit state) when scoreable == 0; surface it in the report "
        "template.\n"
        "TESTS: all-N/A -> 0.0/'Insufficient Scope'; mixed -> unchanged math.",
        priority="medium",
    ),
    _t(
        "nav-comp-02",
        "NAV: Admin burst-detection dead on PG — SQLite datetime() (P2)",
        "tools/dashboard/api/admin.py:100-102,115-117 uses SQLite-only "
        "datetime(%s,%s) under a bare except -> on PostgreSQL it raises and "
        "'except: return None' silently disables anomaly/burst detection on "
        "the primary backend.\n"
        "FIX: compute the time cutoff in Python and pass an ISO timestamp "
        "param (pattern already used in tools/dashboard/api/usage.py:114); "
        "remove the swallowing except or narrow it with logging.\n"
        "TESTS: burst query executes on PG (conftest sqlite ok too); "
        "detection returns rows for seeded burst data.",
        priority="medium",
    ),
    _t(
        "nav-comp-03",
        "NAV: Compliance API family data-authenticity audit-and-fix (P1)",
        "Wave-1 confirmed wiring but not data authenticity for: /api/cato, "
        "/api/control-inheritance, /api/sbd, /api/ai-transparency, "
        "/api/ai-accountability, and pages /logs, /sre, /pr-intel, "
        "/stig-manager, /integrity.\n"
        "DO: for each, verify numbers/statuses come from real tables (no "
        "canned percentages/statuses), errors fail loud (no silent "
        "empty-as-healthy), and mutations are role-gated consistently. Fix "
        "small issues; file follow-up nav- tasks for large ones. Deliver an "
        "audit table in the PR. Compliance pages must never show fabricated "
        "posture (same standard applied to AADC in the PENTA wave).",
    ),
    # ── Epic strat — Strategos ────────────────────────────────────────────
    _t(
        "nav-strat-01",
        "NAV: TRUST-ground INTSUM generation with citations (P1)",
        "tools/strategos/intsum.py:126-158 (prompts) + :161-181 "
        "(_synthesize_paragraph): the 6-paragraph INTSUM is LLM prose from "
        "aggregate signal counts — no [source: ...] citations, no "
        "citation_grounding validation, no provenance. Violates the TRUST "
        "invariant (every LLM-drafted artifact carries inline citations "
        "validated against evidence).\n"
        "FIX: pass concrete source snippet/signal IDs into the prompts, "
        "require inline [source: <id>] citations in output, validate with "
        "tools/quality/citation_grounding.py before persisting, flag/reject "
        "ungrounded paragraphs (citation_guard pattern with HITL force_* "
        "override + audit). REUSE the working doctrine-grounding pattern in "
        "tools/strategos/war_council_generator.py:71-164 — do NOT re-implement "
        "citation parsing.\n"
        "TESTS: generated INTSUM with mocked LLM carries citations that "
        "resolve to provided sources; ungrounded output is flagged/blocked.",
    ),
    _t(
        "nav-strat-02",
        "NAV: TRUST-ground OPORD generation with citations (P1)",
        "tools/strategos/opord.py:20 (PARA_PROMPTS), :100-131 "
        "(synthesize_paragraph), :144 (_llm_call): zero citation/source "
        "handling — same TRUST gap as INTSUM (nav-strat-01). Apply the same "
        "fix: source IDs into prompts, inline [source:] citations, "
        "citation_grounding validation before persist, reject/flag "
        "ungrounded, reuse war_council_generator pattern. Approval flow "
        "(OPORD approve route) must show grounding status to the approver.\n"
        "TESTS: as nav-strat-01, plus approval payload includes grounding "
        "verdict.",
    ),
    _t(
        "nav-strat-03",
        "NAV: Oracle assessment — no fabricated lens scores on failure (P2)",
        "apps/strategos/blueprint.py:1658-1668 returns hardcoded lens scores "
        "(5.0/4.5/5.5/6.0, composite 5.25) when intelligence.oracle."
        "sio_engine fails to import; narratives say 'Mock data' but "
        "composite_score, iw_triggered, and a padded sparkline render as a "
        "real assessment.\n"
        "FIX: return an explicit degraded state ({available: false, reason} "
        "or HTTP 503) and render an 'assessment unavailable' panel — never "
        "numeric scores.\n"
        "TESTS: import-failure path -> available:false, no numeric fields; "
        "healthy path unchanged.",
        priority="medium",
    ),
    _t(
        "nav-strat-04",
        "NAV: Remove broken raw-sqlite3 fallbacks in IW scorers (P2)",
        "tools/strategos/iw_scorers.py:34-42 and iw_bayesian.py:51 fall back "
        "to sqlite3.connect() when get_connection import fails, but the "
        "queries use %s placeholders (iw_scorers.py:65-70) which the stdlib "
        "sqlite3 driver rejects — the 'fail-soft' path itself throws.\n"
        "FIX: delete the raw-sqlite fallback and rely on "
        "tools.db.storage.get_connection() (translate layer handles "
        "placeholders on both backends).\n"
        "TESTS: scorers run under conftest sqlite AND PG; no sqlite3.connect "
        "left in the modules.",
        priority="medium",
    ),
    _t(
        "nav-strat-05",
        "NAV: Remove legacy unregistered tools/strategos/blueprint.py (P2)",
        "tools/strategos/blueprint.py is an unregistered legacy duplicate of "
        "apps/strategos/blueprint.py (defines overlapping /strategos/supply|"
        "darkweb|intel-brief|briefs routes but is never imported in "
        "tools/dashboard/app.py — registration at app.py:2552-2563 uses "
        "apps.strategos). Dead-code confusion risk.\n"
        "FIX: diff the two files first; port anything genuinely unique into "
        "apps/strategos, then delete the legacy file (and its icdev/ mirror "
        "twin if present); grep for imports before removal.\n"
        "TESTS: pytest + dashboard boots; strategos routes unchanged.",
        priority="medium",
    ),
    # ── Epic plat — Platforms menu ────────────────────────────────────────
    _t(
        "nav-plat-01",
        "NAV: FathomDesk synthetic-data honesty flag + banner (P1)",
        "tools/trading/data/market_data.py:126 fetch_bars falls back to "
        "_generate_sample_bars (deterministic LCG random walk, :30) whenever "
        "Alpaca is unavailable; /api/trading/chart/<ticker> "
        "(tools/dashboard/app.py:11011-11036) returns those bars with only a "
        "per-bar source:'sample' field the frontend ignores — fabricated "
        "data presented as market data in a financial UI.\n"
        "FIX: add top-level data_source + simulated:true + as_of to the chart "
        "response; render a prominent 'SIMULATED DATA' banner on the chart "
        "when set; consider the same for other consumers of fetch_bars.\n"
        "TESTS: Alpaca-unavailable path -> simulated:true + banner logic; "
        "live path -> flag absent.",
    ),
    _t(
        "nav-plat-02",
        "NAV: Broker adapter live-mode guard (P1)",
        "tools/fathomdesk/broker_adapter.py: submit_limit_order/"
        "submit_stop_order (:87,:107) POST straight to Alpaca /v2/orders; "
        "is_paper (:55) is just 'paper' in base_url — a misconfigured "
        "ALPACA_BASE_URL executes LIVE orders with no confirmation. "
        "Currently CLI-only (no HTTP route calls submit_*; data_gateway.py:76 "
        "constructs but doesn't submit) — keep it that way.\n"
        "FIX: determine paper/live from explicit config (env var, not URL "
        "substring); require live_confirmed=True argument for any non-paper "
        "submission (raise otherwise); add a regression test asserting no "
        "Flask route reaches submit_* (grep-based or import-graph test).\n"
        "TESTS: live URL without live_confirmed -> raises; paper flow "
        "unchanged; route-exposure test.",
    ),
    _t(
        "nav-plat-03",
        "NAV: GeoSIGINT static-data provenance labels (P1)",
        "apps/geosigint/blueprint.py:80-252: every layer (WEAPON_SYSTEMS, "
        "LANDING_ZONES, THAAD_BATTERIES, CHOKEPOINTS, RADAR_SYSTEMS, "
        "DISRUPTION_SCENARIOS, amphibious fleet, ROCAF bases, ...) is a "
        "hardcoded module constant presented as live intelligence — no DB, "
        "no feed, no as_of/provenance on responses.\n"
        "FIX: attach {data_source: 'static_reference', as_of: '<vintage "
        "date>'} to every layer response; render a persistent 'Reference "
        "data (static)' badge on the GeoSIGINT pages; document the vintage. "
        "(Wiring to live sources is out of scope — labeling only.)\n"
        "TESTS: every layer endpoint carries the provenance fields; badge "
        "renders.",
    ),
    _t(
        "nav-plat-04",
        "NAV: /api/macro/intelligence fail-open NEUTRAL (P2)",
        "tools/dashboard/app.py:10979-10988 returns qeqt_phase:'NEUTRAL', "
        "credit_stress:'NEUTRAL' with HTTP 200 on ANY exception — a data "
        "outage renders as a benign market regime.\n"
        "FIX: on error return 503 (or 200 with explicit "
        "{status:'error'/'stale', detail}) and surface the state in the "
        "consuming UI; log the exception.\n"
        "TESTS: forced failure -> error state visible, not NEUTRAL.",
        priority="medium",
    ),
    _t(
        "nav-plat-05",
        "NAV: GKP endpoints auth depth + tools/geosigint pages audit (P2)",
        "Audit-and-fix two areas wave-1 flagged but did not trace:\n"
        "1. Genesis/Oracle GKP promote/reject endpoints (tools/dashboard/"
        "app.py:9220-9298) — verify mutations are role-gated and audited; "
        "add @require_role if missing (these promote knowledge into the "
        "live system).\n"
        "2. tools/geosigint/ signal/emitter pages (separate from "
        "apps/geosigint) — same criteria as the rest of the sweep: stub "
        "data, fail-open, RBAC, XSS.\n"
        "Fix small issues; file follow-up nav- tasks for large ones; audit "
        "table in PR.",
        priority="medium",
    ),
    # ── Epic misc — Updates + More + systemic ─────────────────────────────
    _t(
        "nav-misc-01",
        "NAV: Persist release-badge seen-version per user (P2)",
        "tools/dashboard/app.py:2199-2204,2818-2819: the Updates nav badge "
        "compares an icdev_seen_version cookie to the brand version — "
        "per-browser, resets on cookie clear.\n"
        "FIX: persist seen-version on the user record (users table column or "
        "user_prefs), fall back to cookie for anonymous; badge clears on "
        "/updates visit for the logged-in user across browsers.\n"
        "TESTS: visit /updates -> badge cleared for that user in a fresh "
        "session; new release -> badge returns.",
        priority="low",
    ),
    _t(
        "nav-misc-02",
        "NAV: Bounded fail-silent policy pass on app.py (P2)",
        "tools/dashboard/app.py contains ~202 'except Exception:' blocks; a "
        "large fraction pass or return empty/canned data so outages render "
        "as healthy empty pages (SYS-1). DO NOT attempt all sites.\n"
        "FIX: (1) document the degraded-state pattern (log + {error:true} "
        "payload + template banner) in a short docs/ note; (2) convert the "
        "worst ~15-20 page-level stats/list endpoints (prioritize: pages "
        "whose entire content silently empties — finetune/connector-forge "
        "are handled in nav-bld-04; pick the next tier by grep survey); "
        "(3) leave a follow-up list in the PR.\n"
        "TESTS: converted endpoints return degraded payload on forced "
        "failure; logs written.",
        priority="medium",
    ),
    _t(
        "nav-misc-03",
        "NAV: Begin app.py decomposition — extract 2-3 route groups (P2 stretch)",
        "tools/dashboard/app.py is ~11,334 lines mixing routing, auth glue, "
        "and business logic (SYS-2). STRETCH task — do only after the rest "
        "of the wave.\n"
        "FIX: extract 2-3 cohesive route groups (candidates: trading/"
        "fathomdesk routes, pulse routes, intake routes) into blueprints "
        "under tools/dashboard/api/ or tools/<domain>/blueprint.py following "
        "the existing blueprint registration pattern; ZERO behavior change; "
        "keep URL paths identical; mirror icdev/ twins via companion sync. "
        "app.py is a merge-conflict hot file — coordinate timing, rebase "
        "frequently, keep the PR mechanical (moves only, no edits).\n"
        "TESTS: full pytest + route-inventory diff empty (same URL map "
        "before/after).",
        priority="low",
    ),
    # ── Epic qa — Verification ────────────────────────────────────────────
    _t(
        "nav-qa-01",
        "NAV: Playwright E2E sweep — 8 menus, RBAC deny-cases, XSS probes",
        "End-of-wave verification (depends on the fix tasks landing; run "
        "last).\n"
        "SPECS: (1) nav-link sweep — every link in the 8 menus (Build, "
        "Intelligence, Compliance, Strategos, Platforms, Studio, Updates, "
        "More) returns 200 and renders its template (extend the existing "
        "e2e:* command patterns); (2) RBAC deny-cases — developer-role "
        "session gets 403 on HITL approve, strategos approve/delete, pulse "
        "publish, admin mutations, usage aggregates; (3) XSS regression — "
        "seed script-tag payloads through the fixed surfaces (PRD, INTSUM "
        "snippet, chat, pulse) and assert inert rendering; (4) honesty "
        "banners — simulated-data banner on FathomDesk chart, static badge "
        "on GeoSIGINT, degraded states render.\n"
        "Claude writes the specs; Kimi executes the Playwright runs (per "
        "args/llm_config.yaml chain_groups.test_execution). Screenshots to "
        "playwright/screenshots/<name>.png.",
    ),
    _t(
        "nav-qa-02",
        "NAV: Full gates + feature doc + memory update",
        "End-of-wave (run after nav-qa-01):\n"
        "1. pytest tests/ -v --tb=short (repo root, absolute PYTHONPATH)\n"
        "2. ruff check .\n"
        "3. python tools/workflow/coherence_checker.py --all --fix --gate\n"
        "4. python tools/dx/companion.py --sync --write --json (FOREGROUND — "
        "never background per memory)\n"
        "5. Write docs/features/nav-menu-readiness.md summarizing the wave "
        "(findings -> fixes -> PRs table)\n"
        "6. Update tools/manifest/ shard entries for any new modules; update "
        "memory files for lessons learned\n"
        "7. Verify the NAV project card renders on Home (/) and hits 100% "
        "when all tasks done (card auto-hides at 100%).",
    ),
]


def main() -> int:
    created = create_tasks(TASKS)
    print(f"created {len(created)} / {len(TASKS)} tasks")
    for tid in created:
        print(f"  + {tid}")
    skipped = [t["id"] for t in TASKS if t["id"] not in created]
    if skipped:
        print(f"skipped (already exist): {len(skipped)}")
        for tid in skipped:
            print(f"  = {tid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
