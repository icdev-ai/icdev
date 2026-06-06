# CUI // SP-CTI — Seed TCH (True Completion Hardening) Kanban tasks
"""Seed kanban_tasks for the True Completion Hardening project (tch-*).

Background: a sibling capability (ACE) shipped "looking done" but had real gaps
(missing icdev/ template mirror, missing IQE wiring). Root-cause analysis found
that the automated completeness gate ``check_new_page_completeness()`` only
inspects files literally named ``page.html`` — so ~44 of ~56 registered canvases
are never checked. SIPA was verified genuinely complete; this project closes the
gate blind spot, remediates the concrete gaps found (slides/mfa/zta icdev
mirrors, IQE triage), and adds continuous + visible enforcement so the problem
cannot recur.

Tasks are seeded to BACKLOG in a single linear depends_on_task_id chain so the
dispatcher implements them in dependency order. Plan reference:
~/.claude/plans/we-re-having-true-completion-joyful-zebra.md
"""


import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402


NOW = datetime.now(timezone.utc).isoformat()


def seed():
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass  # PG backend — PRAGMA is a no-op / unsupported

    tasks = [
        # ── Epic gate: harden the completeness checker (root cause) ─────────
        {
            "id": "tch-gate-01",
            "title": "TCH: Drive new_page_completeness off the _CANVAS_DEFS registry (close the page.html blind spot)",
            "description": (
                "ROOT-CAUSE FIX. tools/workflow/coherence_checker.py::check_new_page_completeness "
                "(@~2786) currently only iterates `templates_dir.rglob('*/page.html')`, so canvases "
                "whose entry template is NOT named page.html (slides=index.html, integrity=index.html, "
                "~44 of ~56 dirs) are never checked.\n"
                "Change: enumerate canvases from tools/dashboard/app.py `_CANVAS_DEFS` (@~142). For each "
                "registered canvas key, resolve its template directory and ENTRY template by precedence: "
                "page.html -> else index.html -> else the template referenced by the blueprint's "
                "render_template(...) call. Run the existing 8-component checks against that entry "
                "template. Keep _load_page_completeness_whitelist() honored for intentional exemptions.\n"
                "Do NOT change the per-component check bodies yet (gate-02/03 refine adapter + mirror "
                "resolution). Keep the CoherenceCheck return shape identical."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": None,
        },
        {
            "id": "tch-gate-02",
            "title": "TCH: Resolve IQE adapter via canvas key -> _CANVAS_MAP (kill false negatives)",
            "description": (
                "CORRECTNESS. Adapter filenames differ from template-dir names: dic.py<->"
                "document_intelligence, security.py<->security_canvas. A naive "
                "`tools/iqe/adapters/<dir>.py` existence check yields false negatives.\n"
                "In check_new_page_completeness, key the IQE checks off the canvas KEY from _CANVAS_DEFS "
                "and resolve the adapter module name via app.py `_CANVAS_MAP` (@~3443). The adapter, "
                "_CANVAS_MAP entry, base.html PATH_CANVAS regex, and context/iqe/queries/<key>/ seed-query "
                "checks must all key off the canvas key (not the directory name).\n"
                "Add a small unit-level sanity assertion that 'dic' and 'sdc' resolve as IQE-complete."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "tch-gate-01",
        },
        {
            "id": "tch-gate-03",
            "title": "TCH: Add full icdev/ mirror-parity sub-check (all canvas templates, not just page.html)",
            "description": (
                "This is what would have caught slides/mfa/zta. Add a sub-check: for every registered "
                "canvas, every tools/dashboard/templates/<dir>/*.html must have a matching "
                "icdev/tools/dashboard/templates/<dir>/*.html mirror. Report each missing mirror as a "
                "violation line `<rel_path>: icdev/ mirror missing`.\n"
                "Respect the whitelist. Keep it cheap (set-diff of filenames per dir, no content compare). "
                "Wire results into the same CoherenceCheck (extend `missing`)."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "tch-gate-02",
        },
        {
            "id": "tch-gate-04",
            "title": "TCH: Unit tests for the hardened completeness checker",
            "description": (
                "tests/test_coherence_new_page_completeness.py. Build synthetic temp-canvas fixtures and "
                "assert:\n"
                "  - a non-page.html canvas (index.html only) IS now checked (regression for the blind spot)\n"
                "  - a complete canvas passes\n"
                "  - missing icdev/ mirror -> fail (slides/mfa/zta class)\n"
                "  - adapter-name-differs-from-dir (dic/sdc style) -> passes (no false negative)\n"
                "  - whitelisted canvas is skipped.\n"
                "Use shim-aware patching where needed; do not depend on live DB."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "tch-gate-03",
        },
        # ── Epic audit: one-shot completion auditor + scorecard ─────────────
        {
            "id": "tch-audit-01",
            "title": "TCH: Create tools/quality/completion_auditor.py (per-canvas scorecard)",
            "description": (
                "New module tools/quality/completion_auditor.py. Scan every _CANVAS_DEFS canvas and emit a "
                "scorecard: per-canvas component-presence matrix (template, icdev mirror, blueprint+route, "
                "backing module, nav link, IQE adapter, seed queries, widget include), a percent-complete, "
                "and an explicit gap list.\n"
                "REUSE the coherence_checker predicates (import the helper functions; do NOT duplicate the "
                "logic). Provide audit_all() -> list[dict] and a __main__ with --json (machine) and --md "
                "(human) flags. Never raise on a single bad canvas — collect and continue."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "tch-gate-04",
        },
        {
            "id": "tch-audit-02",
            "title": "TCH: Auditor CLI report + manifest + commands.md registration",
            "description": (
                "1. --md writes docs/quality/completion-scorecard.md (table sorted least->most complete).\n"
                "2. Add a tool entry to the appropriate tools/manifest/ shard (e.g. tools/manifest/quality.md, "
                "create if absent; add to tools/manifest.md index).\n"
                "3. Document `python tools/quality/completion_auditor.py --json|--md` in "
                "docs/reference/commands.md.\n"
                "Follow the 8-point tool-registration checklist where applicable (no DB tables expected, so "
                "no APPEND_ONLY/conftest entries unless you add a results table)."
            ),
            "task_type": "chore",
            "priority": "medium",
            "depends_on_task_id": "tch-audit-01",
        },
        {
            "id": "tch-audit-03",
            "title": "TCH: Tests for completion_auditor",
            "description": (
                "tests/test_completion_auditor.py. Assert scorecard math (percent = present/total), JSON "
                "shape stability, gap detection for a synthetic incomplete canvas, and that audit_all() "
                "covers all _CANVAS_DEFS keys. No live DB dependency."
            ),
            "task_type": "test",
            "priority": "medium",
            "depends_on_task_id": "tch-audit-02",
        },
        # ── Epic fix: remediate concrete gaps ───────────────────────────────
        {
            "id": "tch-fix-01",
            "title": "TCH: Add missing icdev/ mirror for the slides canvas",
            "description": (
                "tools/dashboard/templates/slides/{index,detail,new}.html have NO "
                "icdev/tools/dashboard/templates/slides/ mirror. Create the mirror via FOREGROUND "
                "`python tools/dx/companion.py --sync --write --json` (NEVER background — background sync "
                "has deleted uncommitted work). Commit any pending slides work FIRST. Verify with "
                "completion_auditor that slides shows mirror-complete."
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "tch-audit-03",
        },
        {
            "id": "tch-fix-02",
            "title": "TCH: Add missing icdev/ mirror for the mfa canvas",
            "description": (
                "tools/dashboard/templates/mfa/*.html has NO icdev/ mirror. Create it via FOREGROUND "
                "companion sync (commit first). Verify mfa shows mirror-complete in the auditor. If mfa is "
                "intentionally not a packaged canvas, instead add it to the gate exemption whitelist with a "
                "documented rationale (decide based on _CANVAS_DEFS membership)."
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "tch-fix-01",
        },
        {
            "id": "tch-fix-03",
            "title": "TCH: Add missing icdev/ mirror for the zta canvas",
            "description": (
                "tools/dashboard/templates/zta/*.html has NO icdev/ mirror. Create it via FOREGROUND "
                "companion sync (commit first). Verify zta shows mirror-complete in the auditor. Same "
                "exemption fallback as tch-fix-02 if zta is not a packaged canvas."
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "tch-fix-02",
        },
        {
            "id": "tch-fix-04",
            "title": "TCH: IQE triage — classify every canvas iqe-required vs iqe-exempt",
            "description": (
                "Run completion_auditor and classify each canvas lacking IQE as either iqe-required "
                "(data-bearing, query-worthy) or iqe-exempt (utility/legacy: admin, agents, events, "
                "monitoring, poam, projects, query, sre, orchestration, autonomous_coder, safety_monitor, "
                "system_graph, il5, etc.).\n"
                "Persist the exempt set in an args/ YAML (e.g. args/completion_exemptions.yaml) consumed by "
                "_load_page_completeness_whitelist, each with a one-line rationale. Output the final "
                "iqe-required list into the task description of tch-fix-05 (or as a committed checklist) so "
                "the backfill is concrete, not guessed."
            ),
            "task_type": "chore",
            "priority": "high",
            "depends_on_task_id": "tch-fix-03",
        },
        {
            "id": "tch-fix-05",
            "title": "TCH: Backfill IQE for canvases flagged iqe-required by tch-fix-04",
            "description": (
                "For EACH canvas marked iqe-required in tch-fix-04, add full IQE integration: "
                "tools/iqe/adapters/<key>.py adapter, _CANVAS_MAP entry in app.py, PATH_CANVAS regex in "
                "base.html, >=3 seed queries in context/iqe/queries/<key>/, and the "
                "{% include 'includes/iqe_query_widget.html' %} in the entry template (+ icdev mirror).\n"
                "This task is expected to be auto-decomposed by the dispatcher into one sub-task per canvas "
                "(the count is small and finalized by tch-fix-04). Verify each via completion_auditor."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "tch-fix-04",
        },
        # ── Epic ci: make the hardened gate blocking (enforcement) ──────────
        {
            "id": "tch-ci-01",
            "title": "TCH: Wire new_page_completeness into the merge/Launch blocking gate",
            "description": (
                "Make the hardened check block merges. Add a new_page_completeness gate entry to "
                "args/security_gates.yaml (block on incomplete canvas). Ensure it runs in: ANVIL Phase 5 "
                "(Launch/merge), the GitHub CI workflow, and the Kanban PR runner. Reuse the existing "
                "`coherence_checker --gate` invocation path rather than adding a parallel runner."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "tch-fix-05",
        },
        {
            "id": "tch-ci-02",
            "title": "TCH: Prove the blocking gate fails an incomplete fixture canvas",
            "description": (
                "Test/dry-run: stand up an intentionally-incomplete fixture canvas (e.g. drop its icdev "
                "mirror), run the gate, assert non-zero/blocking result; restore and assert pass. Keep the "
                "fixture isolated (temp dir) so it does not pollute the real canvas set."
            ),
            "task_type": "test",
            "priority": "medium",
            "depends_on_task_id": "tch-ci-01",
        },
        # ── Epic reflex: continuous autonomous audit ────────────────────────
        {
            "id": "tch-reflex-01",
            "title": "TCH: Genesis completion-audit reflex -> suggested kanban cards",
            "description": (
                "tools/genesis/reflexes/completion_audit.py: run completion_auditor on cadence and write "
                "SUGGESTED kanban cards for newly-detected gaps (mirror "
                "tools/awareness/suggested_card_writer.py; dedupe so the same gap is not re-carded). "
                "Register the reflex EVERYWHERE per the daemon gotchas: REFLEX_NAMES in genesis daemon.py, "
                "the reflex registry, and the reflex config (cadence). Use 127.0.0.1 (not localhost) for any "
                "probe. Feature-flag the card-writing behind an env toggle, default off-safe."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "tch-ci-02",
        },
        {
            "id": "tch-reflex-02",
            "title": "TCH: Tests for the completion-audit reflex",
            "description": (
                "tests/genesis/test_completion_audit_reflex.py. Assert: a seeded gap yields exactly one "
                "suggested card; a clean run yields none; re-running is idempotent (no duplicate cards). "
                "Mock the auditor output; do not depend on the live daemon."
            ),
            "task_type": "test",
            "priority": "medium",
            "depends_on_task_id": "tch-reflex-01",
        },
        # ── Epic dash: completion scorecard visibility ──────────────────────
        {
            "id": "tch-dash-01",
            "title": "TCH: Surface the completion scorecard on an existing route (backing module)",
            "description": (
                "Add a read-only scorecard view by EXTENDING an existing page (prefer the awareness "
                "/components-map area or the system_graph page) rather than spawning a new canvas — avoids "
                "the irony of an incomplete new page. Backing module reads completion_auditor.audit_all(). "
                "Add the route + import with no startup ImportError."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "tch-reflex-02",
        },
        {
            "id": "tch-dash-02",
            "title": "TCH: Scorecard template section + icdev mirror + nav link",
            "description": (
                "Render the per-canvas scorecard (percent + gap chips) in the host template. Mirror the "
                "template change to icdev/ (foreground companion sync). Add/confirm a nav link. Only add an "
                "IQE widget if the host page is itself iqe-required. Verify the new view via Playwright "
                "(screenshot to playwright/screenshots/<name>.png)."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "tch-dash-01",
        },
        # ── Epic vv: verification ───────────────────────────────────────────
        {
            "id": "tch-vv-01",
            "title": "TCH V&V: ruff + full pytest + coherence gate + 0-incomplete auditor",
            "description": (
                "1. ruff check . (exact CI command; a fresh F401 in the lint job is the real blocker).\n"
                "2. pytest tests/ -v --tb=short (SQLite forced by conftest) — green.\n"
                "3. python tools/workflow/coherence_checker.py --all --fix --gate — green, and "
                "new_page_completeness now reports on ALL registered canvases (count ~= len(_CANVAS_DEFS), "
                "not ~12).\n"
                "4. python tools/quality/completion_auditor.py --md — 0 incomplete canvases (or only "
                "documented exemptions). FathomDesk smoke unaffected."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "tch-dash-02",
        },
    ]

    report = create_tasks(
        "tch", tasks, conn=conn, strict=False, register_project=False
    )
    print(report.summary())

    conn.close()
    print(f"\nDone. {len(report.seeded)} tasks seeded to BACKLOG for project tch-*")


if __name__ == "__main__":
    seed()
