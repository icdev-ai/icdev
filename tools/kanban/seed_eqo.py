# CUI // SP-CTI
"""Seed Kanban tasks for EQO — Ecosystem Quality & Observability.

Project: eqo  (task_prefix 'eqo-')  — three parallel epics:
  sipa: make SIPA (tools/integrity) a blocking sibling code-quality gate, ecosystem-wide
  log : centralized logging (queryable DB sink) + 7-day retention + agent autofix
  prd : AI-ify assesses the PRD/requirements for AI-readiness BEFORE the app is built
  vv  : end-to-end verification across all three epics

Plan: C:/Users/schuo/.claude/plans/what-is-the-best-delightful-crab.md

Task IDs follow the <prefix><epic>-<N> convention (eqo-<epic>-NN) so
tools/project/kanban_project_sync.py auto-populates args/projects.yaml.

depends_on_task_id is single-parent (engine limitation, honored by the dispatcher:
a task dispatches only when its parent is 'done'/'decomposed'). Secondary cross-epic
prerequisites are named in the description text. Three roots (eqo-sipa-01, eqo-log-01,
eqo-prd-01) let the epics build in parallel; eqo-vv-01 is the single integration gate.

Run:
    python tools/kanban/seed_eqo.py --dry-run
    python tools/kanban/seed_eqo.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

PROJECT_ID = "eqo"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC sipa — SIPA as a blocking sibling code-quality gate (ecosystem-wide)
    # =========================================================================
    {
        "id": "eqo-sipa-01",
        "title": "tools/integrity/pr_gates.py — assess_changed_files() + --gate/--json CLI + tests",
        "description": (
            "Create tools/integrity/pr_gates.py exposing assess_changed_files(base='origin/main', "
            "mode='auto', conn=None) -> {verdict, risk_score, findings, files_assessed}. Steps: "
            "(1) `git diff --name-only <base>...HEAD` (also support --cached) and keep changed *.py; "
            "(2) stage just those files (with parent dirs so imports resolve) into a temp tree under "
            ".tmp/integrity_quarantine/ via tools/integrity/ingest.stage; (3) run tools/integrity/"
            "scanners.scan_all + tools/integrity/capability_extractor.extract_and_persist + "
            "tools/integrity/scoring.score over the staged subset; (4) return verdict via the same "
            "thresholds, reusing tools/integrity/engine.gate_exit_code for the CLI exit code. REUSE the "
            "existing engine building blocks — do NOT reimplement scanning. Add a __main__ CLI: "
            "`python tools/integrity/pr_gates.py --base origin/main [--cached] --gate --json` (exit 1 on "
            "quarantine, 0 otherwise). Honor ICDEV_INTEGRITY_ENABLED. Tests: clean change -> allow/exit 0; "
            "a planted process_exec/known-bad signature -> quarantine/exit 1; no-py-files -> pass. Root task."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "eqo-sipa-02",
        "title": "validated_commit.py — _run_integrity() sibling gate (block on quarantine)",
        "description": (
            "Add a SIPA gate to tools/workflow/validated_commit.py alongside the existing _run_codelens / "
            "_run_coherence gates. Implement _run_integrity(cwd) -> (passed, reason, metrics) that calls "
            "tools/integrity/pr_gates.assess_changed_files on the working tree, BLOCKS on verdict=="
            "'quarantine' (returns False), warns on 'review', and adds integrity_verdict + risk_score to "
            "the metrics dict. Thread it next to CodeLens+Coherence in validate_working_tree (bump the "
            "ThreadPoolExecutor max_workers), early-exit if it blocks. Skip cleanly (passed=True) when "
            "ICDEV_INTEGRITY_ENABLED is false or tools/integrity is unavailable (ImportError). Tests: a "
            "quarantine verdict makes validate_working_tree return (False, 'SIPA verdict: quarantine...'). "
            "Requires eqo-sipa-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "eqo-sipa-01",
    },
    {
        "id": "eqo-sipa-03",
        "title": "args/security_gates.yaml — SIPA integrity gate policy (block/warn)",
        "description": (
            "Add an 'integrity' gate section to args/security_gates.yaml: block_on: [quarantine], warn_on: "
            "[review], enabled gated by ICDEV_INTEGRITY_ENABLED. DERIVE the verdict tokens from "
            "args/integrity_config.yaml (don't hardcode a competing copy). Document the gate in the "
            "security-gates summary. Requires eqo-sipa-02."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "eqo-sipa-02",
    },
    {
        "id": "eqo-sipa-04",
        "title": "Wire SIPA gate into ANVIL Phase 3 + GitHub CI + Kanban PR runner",
        "description": (
            "Make the SIPA gate ecosystem-wide. (1) Add an explicit SIPA step to ANVIL Phase 3 Tier 1 in "
            ".claude/commands/feature.md (and bug.md/chore.md) calling pr_gates --gate. (2) Add a "
            "'sipa-gate' job to .github/workflows/icdev-ci.yml running "
            "`python tools/integrity/pr_gates.py --base origin/main --gate --json` on PRs (blocking). "
            "(3) Add a SIPA step to .github/workflows/icdev-kanban-runner.yml so every kanban task PR is "
            "integrity-checked before the PR is opened. All steps respect ICDEV_INTEGRITY_ENABLED. "
            "Requires eqo-sipa-02 (secondary: eqo-sipa-03 for the gate policy)."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "eqo-sipa-02",
    },
    {
        "id": "eqo-sipa-05",
        "title": "SIPA gate — manifest/commands/companion/coherence registration + tests",
        "description": (
            "Finalize SIPA-gate registration: document pr_gates.py in tools/manifest/security-scanning.md "
            "+ add the CLI to docs/reference/commands.md; ensure any new tables are in tests/conftest.py "
            "schema; run `python tools/dx/companion.py --sync --write --json` (FOREGROUND); make "
            "`python tools/workflow/coherence_checker.py --all --gate` green for these changes; "
            "`ruff check tools/integrity/pr_gates.py tools/workflow/validated_commit.py` clean and "
            "`pytest tests/test_integrity_pr_gates*.py tests/test_validated_commit*.py -v` green. "
            "Requires eqo-sipa-04 (secondary: eqo-sipa-03)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "eqo-sipa-04",
    },

    # =========================================================================
    # EPIC log — centralized logging + 7-day retention + agent autofix
    # =========================================================================
    {
        "id": "eqo-log-01",
        "title": "Migration — append-only centralized_logs table (RLS) + register",
        "description": (
            "Add a numbered migration under tools/db/migrations/ (NNN_centralized_logs/up.py, follow the "
            "existing pattern) creating an APPEND-ONLY centralized_logs table: (id TEXT PK, ts, component, "
            "level, message, trace_id, session_id, classification, tenant_id, extra_json) with indexes on "
            "(component), (ts), (level). PG-first dual schema; use the RLS-aware get_connection() (global "
            "table, NOT get_canvas_connection) and include tenant_id + classification. Also add the CREATE "
            "to tools/db/init_icdev_db.py, register 'centralized_logs' in APPEND_ONLY_TABLES in "
            ".claude/hooks/pre_tool_use.py, and add it to tests/conftest.py MINIMAL_ICDEV_SCHEMA. Verify "
            "`python tools/db/init_icdev_db.py` runs clean. Root task."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "eqo-log-02",
        "title": "tools/genesis/reflexes/log_ingest.py — tail .logs/*.ndjson into centralized_logs",
        "description": (
            "Create a Genesis reflex tools/genesis/reflexes/log_ingest.py with run(config, trust) that "
            "tails every .logs/*.ndjson file, tracking per-file byte offsets in "
            ".tmp/genesis/log_ingest_offsets.json, and batch-inserts new NDJSON lines into centralized_logs "
            "(parse ts/level/component/message/trace_id/session_id/extra; stamp classification/tenant_id). "
            "Bounded batch size; never raises. Register in THREE places (per the daemon-registration "
            "gotcha): REFLEX_NAMES in tools/genesis/daemon.py, a config entry in args/genesis_config.yaml "
            "(schedule ~every 5m, timeout_seconds ~120), and the reflex registry. Reuse the lightweight "
            "reflex wrapper shape of tools/genesis/reflexes/pr_watcher.py. Tests: seeded ndjson lines land "
            "as rows; re-running does not duplicate (offset respected). Requires eqo-log-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "eqo-log-01",
    },
    {
        "id": "eqo-log-03",
        "title": "7-day configurable retention — purge .logs backups + centralized_logs rows",
        "description": (
            "Set rotation.retention_days: 7 in args/logging_config.yaml (keep it configurable). Create "
            "tools/logging/retention.py with purge(retention_days=None) that (a) deletes .logs/*.ndjson.* "
            "daily backups older than retention_days and (b) DELETEs centralized_logs rows older than "
            "retention_days — this time-based purge is the ONE allowed delete on this append-only table; "
            "document that exception in a module docstring and (if needed) an allowance in "
            ".claude/hooks/pre_tool_use.py. Drive it from a daily reflex tick (extend log_ingest with a "
            "'once per day' guard, or add a tiny daily reflex). Tests: with retention_days=0/1, old files "
            "+ rows are purged, recent ones kept. Requires eqo-log-01."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "eqo-log-01",
    },
    {
        "id": "eqo-log-04",
        "title": "Agent log query API + /logs dashboard page (8-component gate) + log_query.py",
        "description": (
            "Make logs queryable. (1) tools/logging/log_query.py: query_logs(component=None, level=None, "
            "since=None, contains=None, limit=200) -> rows from centralized_logs (RLS via get_connection), "
            "+ a __main__ CLI. (2) GET /api/logs route (same filters as query params). (3) A /logs "
            "dashboard page. This NEW page MUST satisfy the CLAUDE.md 8-component completeness gate: "
            "template tools/dashboard/templates/logs/page.html, mirror to icdev/, @bp.route in a "
            "blueprint, backing module, any constants, graceful-if-missing table handling, a nav link, the "
            "Pages: line in .claude/commands/start.md, AND full IQE integration (tools/iqe/adapters/"
            "logs.py registering a logs collection, POST /api/iqe-query, the iqe_query_widget include, "
            "_CANVAS_MAP + PATH_CANVAS entries, >=3 seed queries in context/iqe/queries/logs/). Tests hit "
            "/api/logs and the page renders. Requires eqo-log-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "eqo-log-02",
    },
    {
        "id": "eqo-log-05",
        "title": "Autofix bridge — centralized_logs anomalies -> Kanban fix cards (all components)",
        "description": (
            "Extend tools/genesis/reflexes/log_triage.py (or add a sibling reflex) to read ERROR/anomaly "
            "events from centralized_logs across ALL components (not just .logs/build.ndjson), classify "
            "them (reuse the existing LLM/rule anomaly scorer + the (component, message_hash) dedup), and "
            "open a Kanban fix card per NEW anomaly signature so the autonomous pipeline troubleshoots/"
            "autofixes (task_type='fix', include the offending component + a log excerpt + a "
            "centralized_logs query to reproduce). Keep the existing dedup-by-signature so it never "
            "re-alerts. Tests: a seeded ERROR row produces exactly one fix card; a duplicate does not. "
            "Requires eqo-log-04 (secondary: eqo-log-02)."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "eqo-log-04",
    },
    {
        "id": "eqo-log-06",
        "title": "Logging — manifest shard + companion/coherence + tests green",
        "description": (
            "Create tools/manifest/logging-system.md documenting icdev_logger, log_ingest, retention, "
            "log_query, the /logs page + IQE adapter, and the autofix bridge; add its index line to "
            "tools/manifest.md. Run `python tools/dx/companion.py --sync --write --json` (FOREGROUND). Make "
            "`python tools/workflow/coherence_checker.py --all --gate` green (schema<->code, manifest, "
            "append-only registration, route/nav parity for /logs, IQE wiring). `ruff check tools/logging/ "
            "tools/genesis/reflexes/log_ingest.py` clean and the logging tests green. "
            "Requires eqo-log-05 (secondary: eqo-log-03)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "eqo-log-05",
    },

    # =========================================================================
    # EPIC prd — AI-ify assesses PRD/requirements for AI-readiness (pre-build)
    # =========================================================================
    {
        "id": "eqo-prd-01",
        "title": "tools/aiify/prd_readiness_assessor.py — score PRD AI-readiness + infer opportunities",
        "description": (
            "Create tools/aiify/prd_readiness_assessor.py: assess_prd_for_ai_readiness(session_id, conn, "
            "use_llm=False) -> {score, components, gaps, opportunities, recommendations}. Read the PRD/"
            "requirements text from intake_sessions + intake_requirements. Score 6 deterministic "
            "components (weights): AI-mention density, data-requirement specificity, integration clarity, "
            "acceptance-criteria quantifiability, governance declarations, IL-model appropriateness. INFER "
            "AI-augmentation opportunities from requirements text by mapping phrases to AI-ify paradigms "
            "in tools/aiify/constants.py (e.g. 'extract X from' -> nlp/regex_user_input; 'generate report/"
            "email' -> string_template_rendering/llm_generation; 'threshold alert' -> hardcoded_threshold/"
            "anomaly_detection; 'search by keyword' -> keyword_list_search/embedding_search). Reuse "
            "tools/aiify/opportunity_scorer.score_and_assess for inferred-opportunity scoring and "
            "tools/requirements/ai_governance_scorer for the governance component. Optional LLM enrichment "
            "behind use_llm (reuse the tools/llm/router.LLMRouter pattern in tools/aiify/pattern_"
            "classifier.py); deterministic path is always the fallback (air-gap safe). Tests on a sample "
            "intake session: a doc that says 'classify and route documents' yields opportunities + a "
            "score; a CRUD-only doc yields 'not_suitable' opportunities. Root task."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "eqo-prd-02",
        "title": "Intake — advisory 'ai_readiness' dimension wired into readiness_scorer",
        "description": (
            "Wire prd_readiness_assessor into tools/requirements/readiness_scorer.py as a new ADVISORY "
            "'ai_readiness' dimension (does NOT hard-block build): after the ai_governance_readiness calc, "
            "call assess_prd_for_ai_readiness(session_id, conn) and include its score in the returned "
            "dimensions (small weight, or reported alongside without changing the gating recommendation). "
            "Persist the score: add an ai_readiness column to readiness_scores (or intake_sessions) via a "
            "small migration. Tests: score_readiness(session_id) now returns an 'ai_readiness' dimension. "
            "Requires eqo-prd-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "eqo-prd-01",
    },
    {
        "id": "eqo-prd-03",
        "title": "PRD generator — 'AI-Readiness Assessment' section",
        "description": (
            "Add an 'AI-Readiness Assessment' section to tools/requirements/prd_generator.py output: list "
            "the inferred AI-augmentation opportunities (from prd_readiness_assessor), the readiness gaps "
            "+ recommendations, and a pointer to /ai-ify for pattern-based code scanning AFTER the app is "
            "built. Keep it advisory/informational. Tests: generate_prd(session_id) markdown contains the "
            "section with at least the inferred opportunities. Requires eqo-prd-01."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "eqo-prd-01",
    },
    {
        "id": "eqo-prd-04",
        "title": "Surface AI-readiness on the intake/readiness dashboard + optional API",
        "description": (
            "Show the new ai_readiness dimension + inferred opportunities on the intake/readiness "
            "dashboard (alongside clarity/feasibility/etc.), and add an optional GET /api/intake/session/"
            "<id>/ai-readiness endpoint returning the assessor output. Reuse the existing readiness UI "
            "pattern; no new standalone canvas page required (extends an existing page — so the 8-component "
            "page gate does not apply unless a brand-new route/page is added). Tests: the endpoint returns "
            "the assessor JSON for a sample session. Requires eqo-prd-02."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "eqo-prd-02",
    },
    {
        "id": "eqo-prd-05",
        "title": "PRD AI-readiness — manifest/companion/coherence + tests green",
        "description": (
            "Document prd_readiness_assessor in tools/manifest/aiify-canvas.md (and/or the requirements "
            "manifest shard). Run `python tools/dx/companion.py --sync --write --json` (FOREGROUND). Make "
            "`python tools/workflow/coherence_checker.py --all --gate` green (schema<->code for the new "
            "ai_readiness column, manifest, config<->code). `ruff check tools/aiify/prd_readiness_"
            "assessor.py tools/requirements/` clean and the readiness/PRD tests green (run "
            "api_surface_extractor before writing tests). Requires eqo-prd-04 (secondary: eqo-prd-02, "
            "eqo-prd-03)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "eqo-prd-04",
    },

    # =========================================================================
    # EPIC vv — end-to-end verification across all three epics
    # =========================================================================
    {
        "id": "eqo-vv-01",
        "title": "End-to-end V&V — SIPA gate + centralized logging + PRD AI-readiness together",
        "description": (
            "Final integration verification across all three epics. (1) SIPA: run "
            "`python tools/integrity/pr_gates.py --base origin/main --gate --json` and confirm "
            "validate_working_tree blocks on a planted quarantine fixture and passes on a benign change. "
            "(2) Logging: run the log_ingest reflex -> rows in centralized_logs; "
            "`python tools/logging/log_query.py --level ERROR --since 1h`; confirm a seeded ERROR opens a "
            "kanban fix card; confirm retention purges with retention_days=1; open /logs in Playwright MCP "
            "and screenshot to playwright/screenshots/logs-*.png. (3) PRD: assess a sample intake session "
            "and confirm the readiness 'ai_readiness' dimension + the PRD AI-Readiness section. Run the "
            "full `pytest tests/ -q` and `python tools/testing/health_check.py --json` green. Write "
            "docs/features/phase-eqo-quality-observability.md summarizing verified behavior. Requires "
            "eqo-prd-05 (secondary: eqo-sipa-05, eqo-log-06)."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "eqo-prd-05",
    },
]


def seed(dry_run: bool = False) -> int:
    now = _now()
    # These tasks are genuinely time-deferred: status 'scheduled' WITH a real
    # scheduled_at (now) — schedule immediately. Stamp status/scheduled_at +
    # the project's "CUI" classification on every task.
    for t in TASKS:
        t.setdefault("task_type", "chore")
        t["status"] = "scheduled"
        t["scheduled_at"] = now
        t["project_id"] = PROJECT_ID
        t["classification"] = "CUI"

    report = create_tasks(
        PROJECT_ID, TASKS, dry_run=dry_run, strict=False, register_project=False
    )
    print(report.summary())
    return len(report.seeded)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed EQO (Ecosystem Quality & Observability) kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
