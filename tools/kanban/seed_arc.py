# CUI // SP-CTI — Seed ARC (Autonomous Recovery v2) Kanban tasks
"""Seed kanban_tasks for the Autonomous Recovery v2 project (arc-*).

Background: the Genesis ``failure_triage`` / ``self_debug`` autonomous-recovery
loop produces real RCA + gated auto-fix, but its diagnosis is shallow because
its EVIDENCE is shallow — ``self_debug.snapshot()`` feeds the LLM ~4 KB of
git-plumbing JSON + a 20-line log tail + grep of quoted error strings, while
the platform ALREADY captures far richer signal it never reads:
``.logs/build.ndjson`` (build_logger.capture_pytest, called at kanban.py:1281),
the ``kanban_verifications`` table (per-gate results, kanban.py:2618),
``centralized_logs`` (migration 181), ``otel_spans`` (@traced), and
``code_lens``. The no-LLM heuristic fallback recognizes ~5 infra patterns and
punts everything else to "manual review, conf 0.30". Confidence is the LLM's
uncalibrated self-report yet is load-bearing at the 0.85 apply gate.

This project deepens the loop into a genuine code-logic debugger, wires it into
the existing logging + observability + visibility surfaces, closes the
patch-held feedback loop to CALIBRATE confidence, and then earns
calibration-gated auto-merge for low-risk task types (ff-only; permanent
ceilings on deploy/migrations/security/hooks/config retained).

Design doc: docs/features/autonomous-recovery-v2-design.md

Tasks are seeded to BACKLOG in a single linear depends_on_task_id chain so the
dispatcher implements them in dependency order. Deployment intent (per design
review): push toward fuller autonomy — calibration-gated auto-merge once
precision is proven.
"""


import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402


NOW = datetime.now(timezone.utc).isoformat()


def seed():
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass  # PG backend — PRAGMA is a no-op / unsupported

    tasks = [
        # ── Epic ev: deep evidence collector (WS1) — the foundation ──────────
        {
            "id": "arc-ev-01",
            "title": "ARC: traceback_analyzer.py — deterministic Python traceback → exception class + file:line:function frames",
            "description": (
                "FOUNDATION (deterministic, no LLM). New module tools/workflow/traceback_analyzer.py. "
                "Parse a blob of verification/build output and extract: exception_type, exception_message, "
                "and an ordered list of frames {file, line, function, code_line, in_repo:bool}. Prefer the "
                "DEEPEST in-repo frame as the primary suspect. Tolerate pytest/unittest/playwright formats "
                "and chained 'During handling of...' tracebacks; return empty result (never raise) on no "
                "match. Public API: parse_traceback(text) -> Traceback dataclass; primary_suspect(tb) -> "
                "'path:line' or None. This is consumed by both arc-ev-02 (evidence) and arc-ev-04 "
                "(heuristic upgrade)."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": None,
        },
        {
            "id": "arc-ev-02",
            "title": "ARC: evidence_collector.py — join build.ndjson + kanban_verifications + centralized_logs + otel_spans + diff",
            "description": (
                "Core of WS1. New module tools/workflow/evidence_collector.py exposing "
                "collect_evidence(task) -> dict (EvidenceBundle). Join sinks that ALREADY exist:\n"
                "  - kanban_verifications latest row (reuse kanban._fetch_verification_details @2608) — "
                "per-gate booleans + reason (tells us WHICH gate failed)\n"
                "  - .tmp/kanban/{task_id}.verification.json (full, untruncated; kanban.py:4793)\n"
                "  - .logs/build.ndjson events for the task window (build_logger format)\n"
                "  - traceback via traceback_analyzer (arc-ev-01) over build+verify output\n"
                "  - tools.logging.log_query.query_logs(component, level>=ERROR, since=window) (centralized_logs)\n"
                "  - otel_spans for the run's trace_id if available (failing span + timing)\n"
                "  - git diff main..kanban/<task> (bounded) + full source of functions named in traceback frames\n"
                "Assemble then PRIORITIZE+TRUNCATE to a configurable char cap (traceback + failing gate + diff "
                "FIRST, plumbing last) so the diagnose prompt stays bounded. Never raise on a missing source — "
                "degrade gracefully and tag which evidence was unavailable."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-ev-01",
        },
        {
            "id": "arc-ev-03",
            "title": "ARC: wire evidence_collector into self_debug.snapshot + failure_triage.diagnose_task",
            "description": (
                "Refactor tools/workflow/self_debug.py::snapshot() (@159) to delegate to "
                "evidence_collector.collect_evidence while PRESERVING all existing keys (back-compat for the "
                "dashboard panel + markers). Update tools/workflow/failure_triage.py::diagnose_task (@306) and "
                "self_debug.diagnose (@216) so the richer bundle is what the LLM sees. Keep the "
                "_DIAGNOSIS_SCHEMA_HINT contract (root_cause/suspect_files/recommendation/patch_hint/confidence) "
                "unchanged. No change to apply gates in this task."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-ev-02",
        },
        {
            "id": "arc-ev-04",
            "title": "ARC: upgrade _heuristic_diagnosis with traceback localization (kill the conf-0.30 cliff)",
            "description": (
                "tools/workflow/self_debug.py::_heuristic_diagnosis (@270) currently returns "
                "'Unknown recurring failure; manual review required' (conf 0.30) for anything outside ~5 infra "
                "patterns. When traceback_analyzer yields a primary in-repo suspect + exception class, return a "
                "REAL deterministic diagnosis: root_cause naming the exception+frame, suspect_files=[primary "
                "suspect], recommendation based on exception class (e.g. AssertionError->patch, "
                "ImportError->patch, TimeoutError->quarantine), and a calibrated confidence (e.g. 0.55-0.65, "
                "below the 0.85 auto-apply bar so it routes to a card without the LLM). Keep the 5 existing "
                "infra rules ahead of the generic path."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "arc-ev-03",
        },
        {
            "id": "arc-ev-05",
            "title": "ARC: tests for traceback_analyzer + evidence_collector + heuristic upgrade",
            "description": (
                "tests/test_arc_evidence.py + tests/test_traceback_analyzer.py. Assert: traceback parsing for "
                "pytest/unittest/chained formats picks the deepest in-repo frame; evidence_collector degrades "
                "gracefully when each sink is absent and respects the truncation cap with traceback/diff "
                "prioritized; the heuristic now returns a real suspect+exception (not conf-0.30) for a sample "
                "AssertionError traceback. Use shim-aware patching (importlib import_module + setattr, NOT "
                "pytest string form) for tools.* monkeypatching; no live DB/LLM."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "arc-ev-04",
        },
        # ── Epic obs: observability + visibility of recovery itself (WS3.1-3.3) ─
        {
            "id": "arc-obs-01",
            "title": "ARC: @traced instrumentation of the triage pipeline + task_id↔trace_id link",
            "description": (
                "WS3.1. Decorate triage_once / diagnose_task / generate_patch / apply_patch_in_worktree "
                "(tools/workflow/failure_triage.py) with @traced (tools/observability/instrumentation.py) so "
                "each recovery becomes a span tree on /traces with timing + GenAI LLM attributes. Honor "
                "args/observability_tracing_config.yaml sampling + content policy (content_tracing.enabled "
                "stays false). If no trace_id is threaded from kanban dispatch→verify (see design Open Q1), "
                "add a cheap task_id↔trace_id correlation row so otel_spans can be joined to a task (also "
                "unblocks evidence_collector's spans field)."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "arc-ev-05",
        },
        {
            "id": "arc-obs-02",
            "title": "ARC: structured NDJSON recovery events → .logs/ (+ centralized_logs)",
            "description": (
                "WS3.2. Emit structured events via get_logger from failure_triage: diagnosis_made, "
                "gate_decision, patch_generated, verify_result, apply_outcome — each with task_id, signature, "
                "confidence, recommendation, outcome. Today outcomes live ONLY in .tmp/kanban/triaged/*.marker. "
                "These flow to .logs/ now and to centralized_logs once log_ingest/direct-write lands → visible "
                "on /logs and queryable via IQE. Keep the marker write (dedup) unchanged."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "arc-obs-01",
        },
        {
            "id": "arc-obs-03",
            "title": "ARC: enrich /api/autonomy/status + _autonomy_status.html (root_cause, diff preview, trace link)",
            "description": (
                "WS3.3 (read-only surfacing — no gate change). Extend tools/dashboard/app.py "
                "/api/autonomy/status (@10649) to include per-diagnosis root_cause, suspect_files, confidence, "
                "a bounded diff preview, verify-output tail, and debugger-iteration count. Update "
                "tools/dashboard/templates/_autonomy_status.html to render them with drill-through links to the "
                "RCA card, the autofix/* branch, and the recovery span on /traces. Mirror the template to "
                "icdev/ via FOREGROUND companion sync. Keep auto-hide-when-idle behavior."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "arc-obs-02",
        },
        {
            "id": "arc-obs-04",
            "title": "ARC: tests + Playwright for tracing, events, and the enriched panel",
            "description": (
                "tests/test_arc_observability.py: assert triage emits the 5 structured event types and that "
                "/api/autonomy/status returns the new fields when a triage marker exists. Playwright: load "
                "Home with a seeded triage marker, confirm the enriched panel renders root_cause + diff "
                "preview + drill-through links; screenshot to playwright/screenshots/arc_recovery_panel.png. "
                "Mock spans/markers; do not depend on the live daemon."
            ),
            "task_type": "test",
            "priority": "medium",
            "depends_on_task_id": "arc-obs-03",
        },
        # ── Epic dbg: deep code-logic debugger (WS2) ────────────────────────
        {
            "id": "arc-dbg-01",
            "title": "ARC: static-analysis-on-diff — run code_lens on changed functions, feed findings into diagnosis",
            "description": (
                "WS2.2 (deterministic, no LLM). In the autofix worktree, run tools/analysis/code_lens.py / Code "
                "Intelligence over ONLY the functions changed in git diff main..kanban/<task>. Surface logic "
                "smells (unhandled branch, None-deref risk, off-by-one, complexity spike, missing return) as "
                "structured findings added to the EvidenceBundle and the diagnose prompt. Bound to changed "
                "functions to keep it cheap. No apply-gate change."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "arc-obs-04",
        },
        {
            "id": "arc-dbg-02",
            "title": "ARC: iterative hypothesis→repro debugger loop (bounded ReAct in the isolated worktree)",
            "description": (
                "WS2.3 — the actual debugger. Replace the one-shot diagnose→patch in failure_triage with a "
                "bounded loop INSIDE the .tmp/autofix/ worktree: hypothesize → instrument (targeted "
                "asserts/log lines) OR re-run the failing test → observe → refine; append each observation to "
                "evidence. Cap max_iterations (default 3, from args/genesis_config.yaml) and a per-failure "
                "token budget. Terminate on confident fix / budget exhaustion / no-progress. Preserve ALL "
                "existing safety: worktree isolation, verification-command allowlist (failure_triage.py:471), "
                "rollback on non-zero verify, 5/hr rate cap. Record iteration count for the panel (arc-obs-03)."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-dbg-01",
        },
        {
            "id": "arc-dbg-03",
            "title": "ARC: opt-in post-mortem locals capture (ICDEV_AUTOFIX_DEEP_DEBUG, worktree-only)",
            "description": (
                "WS2.4 (heavy, executes code → strictly opt-in). Behind a NEW env flag "
                "ICDEV_AUTOFIX_DEEP_DEBUG=true (default off), run faulthandler/trace (or debugpy post-mortem) "
                "on the reproduction in the autofix worktree to capture locals at the failing frame; add them "
                "to evidence. MUST be worktree-only and gated. Add a sandbox-coverage decision in "
                "docs/security/sandbox-coverage.md for this executor (it ingests + runs LLM-influenced repro "
                "code)."
            ),
            "task_type": "build",
            "priority": "low",
            "depends_on_task_id": "arc-dbg-02",
        },
        {
            "id": "arc-dbg-04",
            "title": "ARC: tests for static-on-diff + iterative debugger loop",
            "description": (
                "tests/test_arc_debugger.py. Seed a synthetic logic bug a one-shot diagnose misses and assert "
                "the iterative loop corners it within max_iterations; assert the loop honors the token budget "
                "and rolls back on verify failure; assert static-on-diff flags an injected None-deref. Mock "
                "the LLM (deterministic canned hypotheses); run repro against temp fixtures, never the live "
                "repo."
            ),
            "task_type": "test",
            "priority": "medium",
            "depends_on_task_id": "arc-dbg-03",
        },
        # ── Epic dec: harden the decision (WS4) ─────────────────────────────
        {
            "id": "arc-dec-01",
            "title": "ARC: self-consistency diagnosis — N samples → agreement as real confidence",
            "description": (
                "WS4.1. Before any patch attempt, sample N diagnoses (temperature spread) via the "
                "failure_triage_diagnose route and use cross-sample AGREEMENT (on root_cause class + primary "
                "suspect file) as the confidence that feeds should_auto_apply (failure_triage.py:264), "
                "REPLACING the single self-reported number. Disagreement → suggested-card path. N + token "
                "budget configurable in args/genesis_config.yaml. Persist both raw and self-consistency "
                "confidence for calibration (arc-cal-*)."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-dbg-04",
        },
        {
            "id": "arc-dec-02",
            "title": "ARC: verification beyond the LLM's own command (orig failing test + ruff + coherence)",
            "description": (
                "WS4.2 — close the self-grading gap. Today apply_patch_in_worktree runs only the LLM-supplied "
                "verification_command (failure_triage.py:694), which the LLM also chose. Additionally re-run: "
                "(a) the ORIGINALLY failing test/gate (from kanban_verifications/evidence), (b) ruff check "
                "(exact CI cmd), (c) coherence_checker --gate. Require ALL to pass before commit; any failure "
                "rolls back the worktree. Keep the allowlist + metachar rejection."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-dec-01",
        },
        {
            "id": "arc-dec-03",
            "title": "ARC: tests for self-consistency + expanded verification",
            "description": (
                "tests/test_arc_decision.py. Assert: agreeing samples raise effective confidence and "
                "disagreeing samples force the card path; a patch that passes the LLM command but FAILS the "
                "original test/ruff/coherence is rejected and rolled back. Mock the LLM sampler + the gate "
                "runners; no live DB."
            ),
            "task_type": "test",
            "priority": "medium",
            "depends_on_task_id": "arc-dec-02",
        },
        # ── Epic cal: calibration feedback loop (WS3.4 / Autonomy Phase A) ───
        {
            "id": "arc-cal-01",
            "title": "ARC: triage_runs + triage_outcomes tables (append-only) + registration",
            "description": (
                "Autonomy Phase A. Add two APPEND-ONLY tables (dual SCHEMA_PG/SCHEMA_SQLITE, default PG):\n"
                "  - triage_runs: one row per triage_once cycle (started/finished, scanned, applied, "
                "suggested, autofix_enabled, trace_id).\n"
                "  - triage_outcomes: one row per diagnosis/apply (task_id, signature, signature_class, "
                "task_type, recommendation, confidence_raw, confidence_selfconsistency, gate_decision, "
                "applied, verify_rc, autofix_branch, autofix_commit, merged, held NULL-until-resolved).\n"
                "Register per CLAUDE.md: APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py; new schemas in "
                "tests/conftest.py MINIMAL_ICDEV_SCHEMA; migration under tools/db/migrations/. 'held' is "
                "resolved as a NEW resolution row (never UPDATE an audit row)."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-dec-03",
        },
        {
            "id": "arc-cal-02",
            "title": "ARC: record outcomes + resolve patch-held + rolling precision per cohort",
            "description": (
                "Write a triage_outcomes row on every diagnosis/apply. A resolver (reflex tick or post-merge "
                "hook) marks 'held' when the source task later reaches 'done' without re-failing the same "
                "signature, or 'reverted' otherwise. Compute rolling precision per (task_type, "
                "signature_class) = held / applied over a window. Expose precision via a small helper + "
                "--json CLI for the dashboard (arc-am-03). This is the data that EARNS auto-merge."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-cal-01",
        },
        {
            "id": "arc-cal-03",
            "title": "ARC: tests for calibration recording + precision math",
            "description": (
                "tests/test_arc_calibration.py. Assert: an apply writes exactly one outcomes row; held/reverted "
                "resolution appends (never updates) and flips precision correctly; precision = held/applied per "
                "cohort over the window; append-only constraint is honored. No live daemon."
            ),
            "task_type": "test",
            "priority": "medium",
            "depends_on_task_id": "arc-cal-02",
        },
        # ── Epic am: calibration-gated auto-merge (Autonomy Phase B/C) ──────
        {
            "id": "arc-am-01",
            "title": "ARC: should_auto_merge cohort gate (precision + min-samples + self-consistency, ff-only)",
            "description": (
                "Autonomy Phase B. New gate should_auto_merge(task, diag, cohort_stats) in failure_triage. "
                "Allow ff-only merge ONLY if: all current apply gates pass AND task_type in low-risk set "
                "(start: chore, test) AND cohort has >= MIN_SAMPLES (e.g. 20) applied outcomes AND rolling "
                "precision >= AUTOMERGE_PRECISION (e.g. 0.95) AND self-consistency >= threshold. Thresholds + "
                "cohort set live in args/genesis_config.yaml (NOT just the ICDEV_AUTOFIX_AUTOMERGE env bool). "
                "Reuse _ff_merge_autofix_branch (failure_triage.py:758) — refuses a diverged main. PERMANENT "
                "ceilings retained: deploy, tools/db/migrations/, tools/security/, .claude/hooks/, "
                "args/security_gates.yaml, args/llm_config.yaml, any deny-token signature → human review forever."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "arc-cal-03",
        },
        {
            "id": "arc-am-02",
            "title": "ARC: adaptive threshold revocation + post-merge revert watch",
            "description": (
                "Autonomy Phase C. If a cohort's rolling precision drops below a floor, automatically REVOKE "
                "its auto-merge privilege and notify (Telegram adapter, like self_debug._notify). Add a "
                "post-merge watch: if an auto-merged commit correlates with a NEW failure within a window, "
                "open a revert card. Feature-flag the whole adaptive layer; default conservative."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "arc-am-01",
        },
        {
            "id": "arc-am-03",
            "title": "ARC: dashboard — per-cohort precision + auto-merge status",
            "description": (
                "Extend the enriched panel (arc-obs-03) / an existing route with a per-(task_type, "
                "signature_class) table: applied count, rolling precision, auto-merge status (earned / "
                "probation / revoked / permanently-ceilinged). Read from arc-cal-02's precision helper. Mirror "
                "any template change to icdev/ via FOREGROUND companion sync. Verify with Playwright "
                "(screenshot to playwright/screenshots/arc_calibration.png)."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "arc-am-02",
        },
        {
            "id": "arc-am-04",
            "title": "ARC: tests for the auto-merge gate + revocation",
            "description": (
                "tests/test_arc_automerge.py. Assert: a low-risk cohort with proven precision+samples passes "
                "should_auto_merge; under-sampled or low-precision cohorts do NOT; a ceilinged task_type "
                "NEVER auto-merges regardless of precision; a precision drop revokes the privilege; ff-merge "
                "is refused on a diverged main. Mock cohort_stats + git state; no live merge to real main."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "arc-am-03",
        },
        # ── Epic vv: end-to-end verification ────────────────────────────────
        {
            "id": "arc-vv-01",
            "title": "ARC V&V: ruff + full pytest + coherence gate + live recovery trace check",
            "description": (
                "1. ruff check . (exact CI command; a fresh F401 in the lint job is the real blocker).\n"
                "2. pytest tests/ -v --tb=short (SQLite forced by conftest) — green, incl. all arc-* tests.\n"
                "3. python tools/workflow/coherence_checker.py --all --fix --gate — green.\n"
                "4. End-to-end: seed a synthetic failing kanban task, run one failure_triage cycle, and "
                "confirm: a deep diagnosis citing the traceback frame + diff hunk is produced; the recovery "
                "appears as a span tree on /traces; structured events are queryable on /logs; the enriched "
                "panel shows root_cause + diff preview; a triage_outcomes row is written. FathomDesk smoke "
                "unaffected. Update docs/features/autonomous-recovery-v2-design.md status → shipped."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "arc-am-04",
        },
    ]

    seeded = 0
    for t in tasks:
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id=?", (t["id"],)
        ).fetchone()
        if existing:
            print(f"  SKIP (exists): {t['id']}")
            continue
        conn.execute(
            """INSERT INTO kanban_tasks
               (id, title, description, task_type, priority, status,
                created_at, updated_at, depends_on_task_id, classification)
               VALUES (?, ?, ?, ?, ?, 'backlog', ?, ?, ?, ?)""",
            (
                t["id"], t["title"], t["description"],
                t["task_type"], t["priority"],
                NOW, NOW,
                t.get("depends_on_task_id"),
                "CUI // SP-CTI",
            ),
        )
        print(f"  SEEDED: {t['id']} — {t['title'][:60]}")
        seeded += 1

    conn.commit()
    conn.close()
    print(f"\nDone. {seeded} tasks seeded to BACKLOG for project arc-*")


if __name__ == "__main__":
    seed()
