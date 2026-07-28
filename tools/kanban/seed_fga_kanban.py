#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the FGA project — FORGE Academy + GameDay content wiring — onto the kanban board.

Backs ``docs/spikes/fga-00-academy-gameday-audit-disposition.md``, which dispositions
five external audit documents on /academy and /gameday and records which of their ~40
recommendations survived verification.

Every task is held behind ``fga-gate-00``, seeded ``in_progress`` and never completed
by automation. Nothing here builds until a human reviews the spike and releases the gate.

Usage::

    python tools/kanban/seed_fga_kanban.py            # seed
    python tools/kanban/seed_fga_kanban.py --json     # machine-readable report
    python tools/kanban/seed_fga_kanban.py --dry-run  # print, insert nothing
"""

from __future__ import annotations

import argparse
import json
import sys

SPIKE = "docs/spikes/fga-00-academy-gameday-audit-disposition.md"

GATE_ID = "fga-gate-00"

# Shared preamble appended to every build task. The dispatcher hands the description
# verbatim to an autonomous worker with no other context, so the invariants that would
# otherwise be learned from CLAUDE.md have to travel with the task.
CONTEXT = """
PLATFORM INVARIANTS (this surface has bitten sessions before):
- Academy lives at `apps/forge_academy/`, GameDay at `apps/ai_gameday/` — NOT under
  `tools/`. Templates are `tools/dashboard/templates/forge_academy/` and
  `.../ai_gameday/` + `.../gameday/`.
- `apps/` is MIRRORED under `icdev/apps/`. Editing one side without the other trips
  `test_mirror_drift_baseline` on EVERY branch. Reconcile with `mirror_parity.py --fix`
  before pushing.
- Verify against the root `apps/` + `tools/` copies, never the `icdev/` packaged mirror.
- Both apps are `default_enabled: false`. To exercise them set
  `ICDEV_FORGE_ACADEMY_ENABLED=true` / `ICDEV_GAMEDAY_ENABLED=true`.
- Worktree-first: `git worktree add -b feat/<slug> <path outside repo> origin/main`.
  Never `git checkout -b` in the shared checkout.
- REGRESSION FLOOR — these must stay green (187 passing as of 2026-07-27):
  pytest tests/test_penta_aca_sandbox.py tests/test_penta_aca_content_seed.py \\
         tests/test_penta_aca_routes.py tests/test_penta_aca_oracle.py -q
  pytest tests/test_penta_gd_routes.py tests/test_penta_gd_league.py \\
         tests/test_penta_gd_scoring.py tests/test_penta_gd_schema.py -q
- Do NOT reopen or retitle `penta-*` tasks. PENTA closed 2026-07-18 and is prior art.
"""


def _t(
    task_id: str,
    title: str,
    description: str,
    *,
    priority: str = "medium",
    task_type: str = "build",
    status: str = "backlog",
    depends_on: str | None = None,
) -> dict:
    """Build a task spec.

    Every task except the gate itself declares ``depends_on_task_id``, defaulting to
    ``fga-gate-00``. That FK is what actually blocks auto-dispatch; the ``-gate-00`` id
    suffix only protects the *sentinel* from being promoted/reaped
    (``tools/kanban/gates.py::is_manual_gate``) and does not gate siblings by prefix.
    Omitting the FK would leave every task below immediately dispatchable.
    """
    body = description.strip()
    if task_id != GATE_ID:
        body = body + "\n" + CONTEXT.rstrip() + "\n"
    spec = {
        "id": task_id,
        "title": title,
        "description": body,
        "task_type": task_type,
        "priority": priority,
        "status": status,
        "dispatch_source": "fga_spike_seed",
        "idempotency_key": f"fga-00::{task_id}",
    }
    if task_id != GATE_ID:
        spec["depends_on_task_id"] = depends_on or GATE_ID
    return spec


TASKS: list[dict] = [
    # ------------------------------------------------------------------ gate
    _t(
        GATE_ID,
        "MANUAL-MODE GATE — FGA FORGE Academy + GameDay wiring (held)",
        f"""
MANUAL GATE — do not complete via automation, do not open a PR for this task.

Holds every `fga-*` task until a human has read {SPIKE} and approved the scope.
`promote_backlog_to_scheduled` will not dispatch sibling tasks while this task is open.

WHY GATED: /academy and /gameday are opt-in child apps shipping `default_enabled: false`.
They are worth fixing but should not compete with core platform work for autonomous
runner capacity.

WHAT WAS ALREADY DECIDED (do not re-litigate — see the spike):
- ~9 of the ~40 audited recommendations are real. The rest are refuted by working,
  tested code, or were fixed by the `penta` card on 2026-07-18.
- The reports' headline ask — "author step content for M01-M10, High effort" — is
  INVERTED. That content already exists on disk; it is never ingested.
- Explicitly OUT of scope: authoring the 10 genuinely-empty missions, new missions,
  new GameDay scenarios, and seeding the leaderboard with fabricated profiles
  (fabricated-data paths are what PENTA was chartered to eliminate on this surface).

RELEASE PROCEDURE:
  1) Read {SPIKE}.
  2) Confirm the wire/fix/gd split still matches intent.
  3) Set this task done:
       python tools/kanban/cli.py --set-status fga-gate-00 done
     (.env must be loaded, and origin/main fetched — see the done-verification gate.)
""",
        priority="high",
        task_type="chore",
        status="in_progress",
    ),

    # ------------------------------------------------------------------ wire
    _t(
        "fga-wire-01",
        "Ingest orphaned mission content — discovery mechanism",
        """
WHAT: Academy mission steps are DB-backed and seeded ONLY from the hand-maintained
`BUILTIN_STEPS` dict in `apps/forge_academy/content_loader.py`. The `content/` tree is
never scanned. `content_loader.py:1618` reads:

    if existing == 0 and m["slug"] in BUILTIN_STEPS:
        _seed_steps(conn, mission_id, m["slug"])

A mission absent from that dict gets zero step rows, permanently, and
`mission.html:183-187` renders "No steps found for this mission. Content is being
authored." — even when its markdown is sitting on disk.

WHY: 53 of 89 missions have zero steps. 43 of those have authored content on disk
(199 .md files). This is the single largest real finding in the audit, and the audit
mis-diagnosed it as missing content requiring High-effort authoring.

SCOPE OF THIS TASK: decide and implement the ingestion mechanism only. Prefer
filesystem discovery over generating 43 more hand-written dict entries — discovery
closes the class of bug; more dict entries reproduce it the next time someone adds a
mission. Whatever you choose, `fga-wire-05` must be able to assert the invariant.

TWO ON-DISK LAYOUTS EXIST — handle both:
  a) flat:   content/tier1/m11-multimodal/step-1.md, step-2.md, step-3.md
  b) nested: content/tier1/m01-llm-fundamentals/steps/step1_what_is_an_llm.md
             (+ step1_starter.py, step1_test.py siblings)
The loader already supports both via explicit `content_path` (see the `m-cortex-01`
entry at content_loader.py:1422 using the nested form, and `m11` at :1361 using flat).

MUST ALSO: infer `step_type` (default "coding"; see constants.py for the 7 valid types),
pair `*_starter.py` -> starter_code_path and `*_test.py` -> test_code_path, derive
step_num from the filename ordinal, and derive a human title from the filename slug or
the markdown H1. Do not regress the 36 missions already keyed in BUILTIN_STEPS — explicit
entries must win over discovery.

NOTE: `content_loader.py:1622` swallows all seed exceptions to a log warning. Discovery
failures must not be silent; fail loud or surface a count.

ACCEPTANCE: mechanism implemented and unit-tested; no change yet to the number of
seeded missions is required from THIS task if you land discovery behind wire-02..04.

VERIFY:
  python -c "import sys;sys.path.insert(0,'.');from apps.forge_academy.content_loader import BUILTIN_MISSIONS,BUILTIN_STEPS;s=[m['slug'] for m in BUILTIN_MISSIONS];print('stepless:',len([x for x in s if x not in BUILTIN_STEPS]))"
  pytest tests/test_penta_aca_content_seed.py -q
""",
        priority="high",
    ),
    _t(
        "fga-wire-02",
        "Ingest Tier-1 M01-M10 — the student onboarding path",
        """
WHAT: Wire the 10 Tier-1 missions so they render their authored steps instead of
"No steps found". Depends on the mechanism from fga-wire-01.

WHY: This is the highest-impact slice. M01 LLM Fundamentals is the first card a new
student clicks and is currently a dead end; the audit called it "the single biggest
churn risk in the entire product" and it is the one place where that framing is right.
M01 alone has 5 authored markdown steps plus starter/test .py sitting unused:
  step1_what_is_an_llm.md, step2_token_economics.md, step3_temperature.md,
  step4_context_window.md, step5_llm_router.md

MISSIONS: m01-llm-fundamentals, m02-prompt-engineering, m03-rag-basics, m04-first-agent,
m05-mcp-protocol, m06-fastmcp, m07-multi-agent, m08-strands-agents, m09-langchain,
m10-tier1-capstone (all under apps/forge_academy/content/tier1/).

Note m01 has 5 step files and m02 has 2; the rest have 1 each. Do not fabricate steps
to pad them — ingest what exists.

ACCEPTANCE: all 10 render their steps; step ordering matches the filename ordinal;
coding steps get their starter + test wired so the Run button executes against the real
sandbox (apps/forge_academy/code_runner.py, already hardened by penta-aca-02).

VERIFY: live V&V is mandatory (UI change). With ICDEV_FORGE_ACADEMY_ENABLED=true,
/academy/mission/m01-llm-fundamentals must show 5 steps, and Run Code on a coding step
must return output. Screenshot to playwright/screenshots/fga-m01-steps.png.
""",
        priority="high",
        depends_on="fga-wire-01",
    ),
    _t(
        "fga-wire-03",
        "Ingest Tier-2 orphan batch 1 — role tracks",
        """
WHAT: Wire the Tier-2 orphaned mission directories, batch 1. Depends on fga-wire-01.

WHY: 43 mission dirs total are orphaned; Tier 1 (10) is fga-wire-02, leaving ~33 across
Tier 2/3 split into two batches so each lands as a reviewable PR.

BATCH 1 — the role-track families:
  m-ace-01-roles-delegation, m-ace-02-creator-verifier, m-ace-03-multi-role-pipeline,
  m-ace-capstone, m-readiness-01-eleven-pillars, m-readiness-02-remediation,
  m-readiness-03-continuous, m-docgen-01-session-lifecycle,
  m-docgen-02-portfolio-artifact, m-gov-01-transparency, m-gov-02-accountability,
  m-gov-03-intake, m-gov-capstone
(under apps/forge_academy/content/tier2/{ace-coworker,agent-readiness,docgen,ai-governance}/)

These are exactly the missions the audit listed as "dead end" in Tier 2. Several have
3-4 authored step files each — m-ace-01 has 4.

ACCEPTANCE: every mission in the batch renders its authored steps; none regress to a
single generic step; no new "No steps found" pages in this family.

VERIFY: pytest tests/test_penta_aca_content_seed.py tests/test_penta_aca_content.py -q
plus the stepless-count probe from fga-wire-01.
""",
        depends_on="fga-wire-01",
    ),
    _t(
        "fga-wire-04",
        "Ingest Tier-2/3 orphan batch 2 — remaining families",
        """
WHAT: Wire the remaining orphaned mission directories. Depends on fga-wire-01.

BATCH 2 — the remainder, chiefly:
  m-isso-* (5), m-issm-* (6), m-pm-* (6), m-ciso-* (7), m-sre-* (4 + m-sre-xai-01),
  m-netops-* (4 + m-netops-pna-01), m-secops-* (3), m-devops-* (3),
  m-dataops-01-advanced-rag, m-dataops-02-chromadb-rag, m-swe-01-multi-agent-dag,
  m-swe-02-mcp-server, m-swe-aadc-06/07/08, m-swe-aiml-01/02/03,
  and tier3/m-t3-01..07.

Enumerate authoritatively rather than trusting this list — after wire-02 and wire-03
land, re-run the orphan probe and take whatever remains:
  python -c "import sys,pathlib;sys.path.insert(0,'.');from apps.forge_academy.content_loader import BUILTIN_MISSIONS,BUILTIN_STEPS;print([m['slug'] for m in BUILTIN_MISSIONS if m['slug'] not in BUILTIN_STEPS])"

ACCEPTANCE: the only missions left without steps are the 10 that genuinely have no
content on disk (handled by fga-wire-06). Orphan count reaches 0.

VERIFY: the stepless count must be exactly 10 and the orphan count exactly 0.
""",
        depends_on="fga-wire-01",
    ),
    _t(
        "fga-wire-05",
        "Add the reverse-direction content test that would have caught this",
        """
WHAT: Add a test asserting that every mission directory under
`apps/forge_academy/content/` containing step files is reachable from a seeded mission.

WHY: This is the defect behind the defect. `tests/test_penta_aca_content_seed.py:89`
(`test_every_builtin_step_content_file_exists`) asserts only the FORWARD direction —
every declared step has a file on disk. Nothing asserts the REVERSE — every file on disk
is declared. That asymmetry let 43 mission directories and 199 markdown files rot in the
tree while CI stayed green, and it is why an external audit found this before we did.

IMPLEMENT: in tests/test_penta_aca_content_seed.py, add
`test_no_orphaned_mission_content_dirs`: walk content/tier*/**, collect every directory
holding `step-*.md` or `steps/step*.md`, resolve each to a mission slug, and assert the
slug seeds at least one step row. Failure message must name the orphaned slugs so the
next person sees exactly what to wire.

ALLOWLIST: if any directory is intentionally not a mission (fixtures, shared assets),
put it in an explicit, commented allowlist constant — not a silent skip.

ACCEPTANCE: the test FAILS on a checkout of main before fga-wire-02..04 (prove this and
paste the failure output in the PR), and passes after. It must be cheap — no DB required
if the assertion can run against BUILTIN_STEPS + the filesystem.

VERIFY: pytest tests/test_penta_aca_content_seed.py -q
""",
        priority="high",
        depends_on="fga-wire-01",
    ),
    _t(
        "fga-wire-06",
        "Mark the 10 genuinely-empty missions Coming Soon",
        """
WHAT: 10 catalogued missions have no content on disk at all. Give them an explicit
"Coming Soon" state on the card and the mission page instead of the current raw
"No steps found for this mission. Content is being authored."

MISSIONS (verify the list after wire-02..04 land): m-studio-network-canvas,
m-chat-agent-interview, m-analyst-01-data-intel, m-analyst-02-pattern-detection,
m-analyst-03-report-gen, m-analyst-04-predictive, m-leader-01-ai-maturity,
m-leader-02-roi, m-leader-03-exec-dash, m-leader-04-capstone.

WHY: Authoring these is a product decision, deliberately out of scope for this card. But
shipping a catalogue card that leads to a dead page is the exact experience the audit
described. A student should be able to tell from the grid which missions are playable.

IMPLEMENT: a derived `is_available` on the mission (steps > 0), surfaced as a badge on
the mission card in the browser/hub grid, and a friendlier empty state on the mission
page. Do NOT hard-delete the catalogue entries — they are the authoring backlog.

ACCEPTANCE: no mission card in the grid leads to a bare "No steps found" page; the 10
are visibly distinguishable from playable missions.

VERIFY: live V&V with ICDEV_FORGE_ACADEMY_ENABLED=true; screenshot the grid to
playwright/screenshots/fga-coming-soon.png. pytest tests/test_penta_aca_routes.py -q
""",
        depends_on="fga-wire-04",
    ),

    # ------------------------------------------------------------------- fix
    _t(
        "fga-fix-01",
        "Guild creation 500s on every call — signature mismatch",
        """
WHAT: `POST /api/academy/guild/create` raises TypeError on every request.

EVIDENCE: `apps/forge_academy/blueprint.py:586` calls
    create_guild(name=name, description=description, invite_code=invite_code, created_by=...)
but `apps/forge_academy/db.py:788` is
    def create_guild(name: str, description: str, created_by: int) -> dict
Reproduce without a DB:
    python -c "import sys,inspect;sys.path.insert(0,'.');from apps.forge_academy import db;inspect.signature(db.create_guild).bind(name='X',description='Y',invite_code='c',created_by=1)"
    -> TypeError: got an unexpected keyword argument 'invite_code'

SECOND DEFECT IN THE SAME HANDLER: `db.create_guild` generates its own invite code
internally (db.py:790), so the `invite_code` the route generates at blueprint.py:585 and
returns to the client at :588 is a discarded local value that would NOT match the stored
code even once the signature is fixed. Return `guild["invite_code"]`.

WHY IT SURVIVED: zero test coverage — `grep create_guild tests/` returns nothing. The
matching `join_guild` path is correct (db.py:811), so guild JOIN works and only CREATE
is broken, which is why this reads as a "silent failure" from the UI.

ACCEPTANCE: creating a guild returns 200 with the stored invite code; that code
successfully joins via `POST /api/academy/guild/join`; a regression test covers the
create->join round trip.

VERIFY: new test in tests/test_penta_aca_routes.py (or a new tests/test_fga_guild.py);
pytest -q. Live V&V on /academy/guild.
""",
        priority="high",
    ),
    _t(
        "fga-fix-02",
        "Remove the fake Demo Output block rendered on 27 of 27 watch steps",
        """
WHAT: `tools/dashboard/templates/forge_academy/partials/_step_watch.html:20-34` has a
hardcoded `{% else %}` fallback that renders a generic LLMRouter snippet under a
"▶ Demo Output" heading whenever a watch step's `config_schema` supplies neither
`demo_output` nor `demo_url`.

BLAST RADIUS: every seeded watch step. Measured:
    python -c "import sys;sys.path.insert(0,'.');from apps.forge_academy.content_loader import BUILTIN_STEPS;w=[t for v in BUILTIN_STEPS.values() for t in v if t.get('step_type')=='watch'];print(len(w),'watch steps;',len([t for t in w if not (t.get('config_schema') or {}).get('demo_output') and not (t.get('config_schema') or {}).get('demo_url')]),'with no demo')"
    -> 27 watch steps; 27 with no demo
The audit reported this on 8 missions ("template injection"); the real count is 27.

WHY IT IS WORSE THAN COSMETIC: the snippet is WRONG CODE. It shows
    provider = router.get_provider_for_function("chat")
    response = provider.chat(messages=[...])
but `get_provider_for_function` returns a TUPLE, so `.chat()` would raise AttributeError.
A training platform is teaching a call that does not work against its own API, labelled
as real output. It also imports via the legacy `tools.llm.router` shim; new code must use
`icdev.tools.llm.router` (CLAUDE.md import conventions).

FIX: delete the fallback. A watch step with no demo should render no Demo Output panel at
all. Do NOT replace it with a different generic snippet — that reintroduces fabricated
content, which is precisely what PENTA was chartered to remove from this surface. If a
demo genuinely belongs on specific steps, author `demo_output` per step in a follow-up.

ACCEPTANCE: 0 watch steps render a snippet they did not author; the panel is absent (not
empty-boxed) when no demo is configured.

VERIFY: the probe above must report 0 rendered fallbacks; live V&V on any leadership
mission (e.g. m-leadership-01-ai-roi step 1). pytest tests/test_penta_aca_content.py -q
""",
        priority="high",
    ),
    _t(
        "fga-fix-03",
        "Profile save drops display_name and writes a tenant_id orphan row",
        """
WHAT: `POST /api/academy/user/setup` (`apps/forge_academy/blueprint.py:366-380`) has
three distinct data-loss defects:

1. TENANT MISMATCH (the serious one): `:373` calls
   `get_or_create_user(email, display_name=...)` WITHOUT `tenant_id`, while every page
   reads the user via `_fa_user()` (`:131`) WITH `tenant_id=_fa_tenant_id()`. In
   multi-tenant mode the role is written to a `tenant_id IS NULL` row that no page ever
   reads, so the profile silently never takes effect.
2. `display_name` is POSTed by `profile.html:191` but `get_or_create_user` returns an
   existing row without updating it — the submitted name is discarded.
3. `wizard_answers` are computed into `wizard_result` at `:378`, returned to the client,
   and never persisted.

WHY: the audit reported "LOCK IN PROFILE gives no feedback / did my profile save?" and
graded it a no-op. The role DOES persist in single-tenant mode (`:375` update_user_role
-> db.py:442), so the report is wrong that nothing saves — but in multi-tenant it is
effectively true, and the user gets no confirmation either way.

FIX: pass tenant_id consistently on the write path; update display_name when supplied;
persist wizard answers or stop computing them. Return a response the UI can act on and
surface a confirmation in profile.html.

ACCEPTANCE: profile round-trips under BOTH single- and multi-tenant contexts; the value
written is the value `_fa_user()` reads back; the UI confirms.

VERIFY: regression test asserting write-then-read parity through `_fa_user()` with a
tenant set. pytest tests/test_penta_aca_routes.py -q. Live V&V on /academy/profile.
""",
    ),
    _t(
        "fga-fix-04",
        "Arena is permanently empty — fa_challenges has zero INSERTs",
        """
WHAT: `/academy/arena` always shows "No Active Challenges".
`apps/forge_academy/blueprint.py:335-337` selects
`fa_challenges WHERE ends_at > now()`, but repo-wide `fa_challenges` is only ever
CREATEd and SELECTed — there are ZERO INSERT statements, no seeder, and no admin-create
route. The entry API at `blueprint.py:616` writes `fa_challenge_entries` and is
unreachable because no challenge can exist to enter.

DECIDE ONE (state the choice in the PR):
  a) Seed real challenges derived from existing missions, with a rotation, OR
  b) Hide the Arena nav link behind a capability check until challenges exist.

Do NOT seed fabricated leaderboard-style filler to make the page look populated —
fabricated data presented as real is the failure mode PENTA removed from this surface.
Option (b) is the honest default if no challenge model is wanted yet.

ACCEPTANCE: either the Arena shows genuine challenges that can be entered end to end, or
it is not reachable from navigation. No dead page either way.

VERIFY: pytest tests/test_penta_aca_routes.py -q (adjust the route assertion to match the
chosen option). Live V&V on /academy and /academy/arena.
""",
    ),
    _t(
        "fga-fix-05",
        "Workflow Builder palette emptied by a swallowed import failure",
        """
WHAT: `/academy/workflow-builder` renders "No patterns available." and the canvas stays
empty. The backend is real — `blueprint.py:637-658` -> `integrations.create_workflow`
(`integrations.py:83-93`) -> `tools.aisg.visual_agent_builder`, writing
`fa_workflow_submissions` and awarding XP.

ROOT CAUSE: `apps/forge_academy/integrations.py:53-59` wraps the pattern-registry import
in a bare try/except, logs a `warning`, and returns `[]`. `blueprint.py:353` feeds that
empty list to the template (`workflow_builder.html:89`). A dependency failure is
therefore indistinguishable from "no patterns configured", and nothing surfaces to the
user or to health checks.

FIX: stop swallowing. Determine why the import fails in a default environment and either
fix the dependency or make the failure explicit — log at error with the exception, and
render a distinguishable state ("pattern registry unavailable") rather than the same
empty-list path used when there legitimately are no patterns.

ACCEPTANCE: with the registry importable, the palette populates and a workflow can be
generated; with it broken, the page says so and the failure appears in logs/health.

VERIFY: python -c "import sys;sys.path.insert(0,'.');from apps.forge_academy import integrations;print(len(integrations.list_patterns()))"
Live V&V on /academy/workflow-builder. pytest tests/test_penta_aca_routes.py -q
""",
    ),
    _t(
        "fga-fix-06",
        "Academy hub ignores ?role= while every other page honours it",
        """
WHAT: The "View as:" persona dropdown does not filter the hub.

EVIDENCE: `missions_browser()` (`apps/forge_academy/blueprint.py:191`),
`leaderboard_page()` (`:287`) and `api_leaderboard()` (`:611`) all read
`request.args.get("role")` and apply it. `hub()` (`:163-181`) reads NO `request.args` —
it hardcodes `list_missions(role=fa_user.get("role"), ...)` at `:171`, i.e. the saved
profile role.

So the audit's "the ?role= parameter is ignored by the backend, every persona sees
identical content" is HALF right — it is true on the hub and false everywhere else. The
inconsistency is the bug: the same control behaves differently depending on the page.

FIX: honour an explicit `?role=` on the hub, falling back to the profile role when
absent. Make the dropdown actually navigate (set the query param) and reflect the active
selection on load instead of resetting to "All (Full View)".

ACCEPTANCE: selecting a persona changes the hub mission set and the selection survives a
refresh; behaviour matches the missions browser.

VERIFY: route test asserting a filtered hub for two different roles.
pytest tests/test_penta_aca_routes.py -q. Live V&V across 2-3 personas.
""",
        priority="low",
    ),
    _t(
        "fga-fix-07",
        "Unenrolled users get silent no-ops on every progress action",
        """
WHAT: When no Academy user record resolves, every XP/progress write silently does
nothing and the UI gives no indication.

EVIDENCE: `mission.html:190` sets `const FA_USER_ID = {{ fa_user.id if fa_user else 'null' }}`
and `submitStep()` (`mission.html:248-249`) opens with `if (!FA_USER_ID) return;`. The
whole function — POST to /api/academy/step/submit, XP toast, level-up, nav "done" marking
— is skipped. The caller then proceeds as if it succeeded. The step-completion handlers
in the partials (e.g. `_step_watch.html:47-50`) await it and continue regardless.

WHY THIS MATTERS: this is the most likely cause of the audit's central experience report
— "I clicked, the button clicked, but nothing changed, no confirmation, no score update."
The mechanics are not broken; they are inert for a user the system does not recognise,
and it never says so. Note `hub()` (`:167-168`) redirects unknown users to /academy/profile,
so this state is reachable whenever profile setup silently fails (see fga-fix-03).

FIX: when `FA_USER_ID` is null, surface it — a persistent banner on the mission page
("You are not enrolled; progress will not be saved") and/or an inline message on the
first blocked action. Do not fail hard and do not block reading the content.

ACCEPTANCE: an unenrolled visitor can still read a mission but is told, before acting,
that progress will not persist. No action appears to succeed while doing nothing.

VERIFY: live V&V with a session that has no fa_user row. pytest tests/test_penta_aca_routes.py -q
""",
    ),

    # -------------------------------------------------------------------- gd
    _t(
        "fga-gd-01",
        "GameDay scenario manager and builder are unreachable from the hub",
        """
WHAT: `/gameday/scenarios` (`apps/ai_gameday/blueprint.py:178`, renders
`ai_gameday/scenario_manager.html`) and `/gameday/scenarios/builder` (`:211`, renders
`ai_gameday/scenario_builder.html`) both exist, are `@require_facilitator`, and are
asserted live in `tests/test_penta_gd_routes.py:356-357`.

But `tools/dashboard/templates/ai_gameday/hub.html` contains ZERO references to either:
    grep -c "gameday/scenarios" tools/dashboard/templates/ai_gameday/hub.html  ->  0
Its only outbound links are ai-league, facilitate, leaderboard, simulate and results. The
builder is linked only from scenario_manager.html, which is itself orphaned. The feature
is reachable only by typing the URL.

The audit recorded this as "Build Scenario page is 404 — cannot create custom scenarios",
having tried `/gameday/scenario/build`, a URL that never existed. The 404 claim is wrong;
the reachability gap is real.

FIX: link the scenario manager from the hub, gated on facilitator role so non-facilitators
do not see a link that 403s.

ACCEPTANCE: a facilitator can reach the scenario builder from /gameday without typing a
URL; a non-facilitator sees no dead link.

VERIFY: pytest tests/test_penta_gd_routes.py -q plus an assertion that hub.html links the
manager. Live V&V on /gameday as a facilitator.
""",
    ),
    _t(
        "fga-gd-02",
        "12 of 34 GameDay tool links are POST-only APIs rendered as GET anchors",
        """
WHAT: The player console's AI Tools sidebar renders every catalog entry as a plain link:
`tools/dashboard/templates/ai_gameday/player.html:98`
    <a class="ai-tool-btn" href="{{ tool.endpoint }}" target="_blank">
But 12 of the 34 entries in `AI_TOOLS_CATALOG` (`apps/ai_gameday/constants.py:61-103`)
are POST-only `/api/...` endpoints, so a click opens a new tab that 405s or 404s.

Measured:
    python -c "import sys;sys.path.insert(0,'.');from apps.ai_gameday.constants import AI_TOOLS_CATALOG;print(len(AI_TOOLS_CATALOG),'entries;',len([t for t in AI_TOOLS_CATALOG if t['endpoint'].startswith('/api/')]),'are /api paths')"
    -> 34 entries; 12 are /api paths
Offenders include /api/strategos/oracle, /api/strategos/signals, /api/strategos/wargame,
/api/strategos/iw/composite, /api/strategos/simulate/run, /api/finetune/jobs,
/api/knowledge/search, /api/ace/coworker/delegate, /api/readiness/check,
/api/readiness/remediate. TWO additionally contain unresolved placeholders and can never
work as links: /api/strategos/wargame/{id}/ooda and /api/ace/coworker/{id}/result.

The other 22 point at real pages (/cortex, /knowledge, /network, /kanban,
/agentic-ai/canvas) and work correctly — so the audit's "34 buttons, 0 functional" is
wrong, but a third of them are genuinely dead.

IMPORTANT — do not "fix" this by building a tool proxy. The per-inject buttons already
implement the intended receipts model: `player.html:186` onclick="linkTool(...)" ->
`:213-226` POST /api/gameday/api-log -> `blueprint.py:466-495` -> `tools/ttx/engine.py:202-230`
INSERT into ttx_api_log. That is the scoring path and it works.

FIX: give each catalog entry an explicit kind (navigable page vs API reference). Render
only navigable entries as links; render API entries as non-clickable reference text, or
point them at the owning page. Drop or repair the two `{id}` placeholder entries.

ACCEPTANCE: no sidebar link opens a 404/405 tab; receipt logging via the per-inject
buttons is unchanged.

VERIFY: a test asserting every rendered-as-link endpoint resolves to a GET route.
pytest tests/test_penta_gd_routes.py -q. Live V&V clicking through the sidebar.
""",
    ),
    _t(
        "fga-gd-03",
        "VERIFY-BEFORE-FIX — reproduce the scenario-picker claim against a live session",
        """
WHAT: This task is an investigation, not a fix. Do not change behaviour unless it
reproduces.

THE CLAIM: the audit reports the scenario picker is cosmetic and all 9 scenarios serve
the same 6 `ai_gameday` injects; it says 9 sessions were created from different dropdown
selections and every one showed identical injects.

WHAT THE TREE SAYS (contradicts the claim):
- 9 distinct packs exist under `scenarios/` with DIFFERENT inject counts — ai_gameday 7,
  forge_ascent 6, grounding-red-team 6, hunt_the_fleet 6, meridian 6, document-integrity 5,
  red_team_the_ai 5, slo-meltdown 5, interagency inline.
- The picker is wired end to end: hub.html:70-71 select -> :155,159,161 posts
  {scenario_slug} -> blueprint.py:318 data.get("scenario_slug", SCENARIO_SLUG) ->
  tools/ttx/engine.py:42 load_scenario(slug) -> :56 seed_injects(session_id, scenario).
- There is NO fallback-to-default path: an unknown slug raises FileNotFoundError
  (scenario_loader.py:31) -> 400.

TASK: create one session per scenario with ICDEV_GAMEDAY_ENABLED=true and compare the
seeded injects. Two credible explanations to rule out: (a) the reporter read the injects
of a previously-created session from the Active Sessions list rather than the one just
created; (b) the default in `data.get("scenario_slug", SCENARIO_SLUG)` is being hit
because the form field name does not match what the handler reads.

OUTCOME: if injects differ per scenario, close this task with the evidence and record the
claim as refuted — no code change. If they do not, file the root cause and fix it.

VERIFY: paste per-scenario inject titles/counts in the PR or the closing comment.
pytest tests/test_penta_gd_ttx_scenarios.py -q
""",
        priority="low",
    ),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Seed FGA FORGE Academy + GameDay content-wiring tasks"
    )
    ap.add_argument("--json", action="store_true", help="JSON report to stdout")
    ap.add_argument("--dry-run", action="store_true", help="Print, insert nothing")
    args = ap.parse_args(argv)

    if args.dry_run:
        report = {
            "dry_run": True,
            "count": len(TASKS),
            "tasks": [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "status": t["status"],
                    "depends_on": t.get("depends_on_task_id"),
                }
                for t in TASKS
            ],
        }
        print(json.dumps(report, indent=2))
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)

    report = {
        "created": created,
        "created_count": len(created),
        "submitted_count": len(TASKS),
        "skipped_existing": [t["id"] for t in TASKS if t["id"] not in created],
        "gate": GATE_ID,
        "spike": SPIKE,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Seeded {len(created)}/{len(TASKS)} FGA tasks (gate: {GATE_ID})")
        for tid in created:
            print(f"  + {tid}")
        if report["skipped_existing"]:
            print("  (already present: " + ", ".join(report["skipped_existing"]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
